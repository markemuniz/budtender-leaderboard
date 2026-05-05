from flask import Flask, render_template, request, redirect, jsonify, url_for, flash
import pandas as pd
import json
import os
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tta-budtender-lb-2026')

EXCLUDED_NAMES = {'Jude C', 'Mark M', 'Jeff M', 'Kevin Y', 'Allen P'}
EXCLUDED_CONTAINS = 'DTBK'

# On Railway, mount a volume at /data for persistence
DATA_DIR    = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
DATA_FILE   = os.path.join(DATA_DIR, 'leaderboard.json')
UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {'dates_included': [], 'budtenders': [], 'last_updated': None}


def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def process_excel(filepath):
    meta = pd.read_excel(filepath, sheet_name='Report', header=None)
    try:
        date_str = pd.to_datetime(str(meta.iloc[1, 1])).strftime('%m/%d/%Y')
    except Exception:
        date_str = datetime.now().strftime('%m/%d/%Y')

    df = pd.read_excel(filepath, sheet_name='Report', header=4)
    df = df[~df['Budtender Name'].astype(str).str.contains(EXCLUDED_CONTAINS, case=False, na=False)]
    df = df[~df['Budtender Name'].isin(EXCLUDED_NAMES)]
    df = df[df['Budtender Name'].notna() & (df['Budtender Name'].astype(str).str.strip() != '')]

    summary = df.groupby('Budtender Name').agg(
        total_units=('Total Inventory Sold', 'sum'),
        total_transactions=('Order ID', 'nunique')
    ).reset_index()
    summary['upt'] = summary['total_units'] / summary['total_transactions']
    return date_str, summary


def merge_into_leaderboard(existing, date_str, new_summary):
    if date_str in existing['dates_included']:
        return existing, False, f'{date_str} has already been imported'

    existing['dates_included'].append(date_str)
    existing['last_updated'] = datetime.now().strftime('%m/%d/%Y %I:%M %p')

    bmap = {b['name']: b for b in existing['budtenders']}

    for _, row in new_summary.iterrows():
        name = row['Budtender Name']
        upt = float(row['upt'])
        units = int(row['total_units'])
        txns = int(row['total_transactions'])

        if name in bmap:
            b = bmap[name]
            b['daily_upt'][date_str] = round(upt, 4)
            b['daily_units'][date_str] = units
            b['daily_transactions'][date_str] = txns
            b['total_units'] = sum(b['daily_units'].values())
            b['total_transactions'] = sum(b['daily_transactions'].values())
            b['days_present'] = len(b['daily_upt'])
            b['avg_upt'] = round(sum(b['daily_upt'].values()) / len(b['daily_upt']), 4)
        else:
            bmap[name] = {
                'name': name,
                'days_present': 1,
                'total_units': units,
                'total_transactions': txns,
                'avg_upt': round(upt, 4),
                'daily_upt': {date_str: round(upt, 4)},
                'daily_units': {date_str: units},
                'daily_transactions': {date_str: txns},
            }

    existing['budtenders'] = sorted(bmap.values(), key=lambda x: x['avg_upt'], reverse=True)
    return existing, True, f'Successfully imported {date_str} — {len(new_summary)} budtenders processed'


def rebuild_without_date(data, date_to_remove):
    data['dates_included'] = [d for d in data['dates_included'] if d != date_to_remove]
    updated = []
    for b in data['budtenders']:
        for store in ('daily_upt', 'daily_units', 'daily_transactions'):
            b.setdefault(store, {}).pop(date_to_remove, None)
        if b['daily_upt']:
            b['days_present'] = len(b['daily_upt'])
            b['avg_upt'] = round(sum(b['daily_upt'].values()) / len(b['daily_upt']), 4)
            b['total_units'] = sum(b.get('daily_units', {}).values())
            b['total_transactions'] = sum(b.get('daily_transactions', {}).values())
            updated.append(b)
    data['budtenders'] = sorted(updated, key=lambda x: x['avg_upt'], reverse=True)
    data['last_updated'] = datetime.now().strftime('%m/%d/%Y %I:%M %p')
    return data


@app.route('/')
def leaderboard():
    return render_template('leaderboard.html')


@app.route('/admin')
def admin():
    return render_template('admin.html', data=load_data())


@app.route('/api/leaderboard')
def api_leaderboard():
    return jsonify(load_data())


@app.route('/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    if not f or f.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('admin'))
    if not f.filename.lower().endswith(('.xlsx', '.xls')):
        flash('Please upload an Excel (.xlsx) file.', 'error')
        return redirect(url_for('admin'))

    path = os.path.join(UPLOAD_FOLDER, secure_filename(f.filename))
    f.save(path)
    try:
        date_str, summary = process_excel(path)
        data = load_data()
        data, ok, msg = merge_into_leaderboard(data, date_str, summary)
        if ok:
            save_data(data)
        flash(msg, 'success' if ok else 'warning')
    except Exception as e:
        flash(f'Error processing file: {e}', 'error')
    return redirect(url_for('admin'))


@app.route('/api/upload', methods=['POST'])
def api_upload():
    f = request.files.get('file')
    if not f or f.filename == '':
        return jsonify({'ok': False, 'type': 'error', 'msg': 'No file selected.'})
    if not f.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'ok': False, 'type': 'error', 'msg': 'Please upload an Excel (.xlsx) file.'})

    path = os.path.join(UPLOAD_FOLDER, secure_filename(f.filename))
    f.save(path)
    try:
        date_str, summary = process_excel(path)
        data = load_data()
        data, ok, msg = merge_into_leaderboard(data, date_str, summary)
        if ok:
            save_data(data)
        return jsonify({'ok': ok, 'type': 'success' if ok else 'warning', 'msg': msg, 'data': load_data()})
    except Exception as e:
        return jsonify({'ok': False, 'type': 'error', 'msg': f'Error processing file: {e}'})


@app.route('/remove-date', methods=['POST'])
def remove_date():
    date = request.form.get('date')
    data = load_data()
    if not date or date not in data['dates_included']:
        flash('Date not found.', 'error')
        return redirect(url_for('admin'))
    data = rebuild_without_date(data, date)
    save_data(data)
    flash(f'Removed {date} from leaderboard.', 'success')
    return redirect(url_for('admin'))


@app.route('/remove-budtender', methods=['POST'])
def remove_budtender():
    name = request.form.get('name')
    data = load_data()
    before = len(data['budtenders'])
    data['budtenders'] = [b for b in data['budtenders'] if b['name'] != name]
    after = len(data['budtenders'])
    if before != after:
        save_data(data)
        flash(f'Removed {name} from leaderboard.', 'success')
    else:
        flash(f'{name} not found.', 'error')
    return redirect(url_for('admin'))


@app.route('/clear', methods=['POST'])
def clear():
    save_data({'dates_included': [], 'budtenders': [], 'last_updated': None})
    flash('All leaderboard data cleared.', 'success')
    return redirect(url_for('admin'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
