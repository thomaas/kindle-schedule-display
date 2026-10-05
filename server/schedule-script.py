#!/usr/bin/env python3

# Kindle Schedule Display
# Fetches today's timetable from Schulmanager Online and renders it into an
# 800x600 (landscape) SVG, analogous to weather-script.py.
#
# Usage:
#   python3 schedule-script.py                 # fetch and render
#   python3 schedule-script.py --dump-json     # also print raw API data
#   python3 schedule-script.py --from-json f   # render from saved API data
#   python3 schedule-script.py --date 2026-10-05
#   python3 schedule-script.py --no-weather    # skip the weather forecast
#   python3 schedule-script.py --fish-name     # only print a fish name for fishdraw

import argparse
import codecs
import configparser
import datetime
import hashlib
import json
import os
import re
import sys
from xml.sax.saxutils import escape

import requests

API_BASE_URL = 'https://login.schulmanager-online.de'
WEATHER_API_URL = 'https://api.open-meteo.com/v1/forecast'
FALLBACK_BUNDLE_VERSION = '3505280ee7'

DAYS_OF_WEEK = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))



#
# Configuration
#

def load_config():
    config = configparser.ConfigParser()
    config.read(os.path.join(SCRIPT_DIR, 'schedule-config.ini'), encoding='utf-8')
    section = config['schulmanager'] if config.has_section('schulmanager') else {}
    weather = config['weather'] if config.has_section('weather') else {}
    return {
        'email': os.environ.get('SM_EMAIL') or section.get('email', ''),
        'password': os.environ.get('SM_PASSWORD') or section.get('password', ''),
        'switch_time': section.get('switch_time', '15:00'),
        'latitude': float(weather.get('latitude', '48.2946')),
        'longitude': float(weather.get('longitude', '10.1055')),
        'rain_threshold': int(weather.get('rain_threshold', '40')),
    }



#
# Schulmanager API
#

def fetch_bundle_version(session):
    # The API expects the version hash of the current web app bundle
    try:
        html = session.get(API_BASE_URL, timeout=30).text
        for js_path in re.findall(r'src="(/[^"]*\.js[^"]*)"', html):
            js = session.get(API_BASE_URL + js_path, timeout=30)
            if js.status_code == 200:
                match = re.search(r'bundleVersion["\s:]+["\']([a-f0-9]{8,})["\']', js.text)
                if match:
                    return match.group(1)
    except requests.RequestException:
        pass
    return FALLBACK_BUNDLE_VERSION


def login(session, email, password):
    salt = session.post(API_BASE_URL + '/api/get-salt',
                        json={'emailOrUsername': email, 'mobileApp': False}, timeout=30)
    salt.raise_for_status()
    salt = salt.json()
    if isinstance(salt, dict):
        salt = salt.get('salt', '')

    # PBKDF2-SHA512, 99999 iterations, 512 byte output (as in the web app)
    salted_hash = hashlib.pbkdf2_hmac('sha512', password.encode('utf-8'), salt.encode('utf-8'),
                                      99999, dklen=512).hex()

    resp = session.post(API_BASE_URL + '/api/login',
                        json={'emailOrUsername': email, 'password': password,
                              'hash': salted_hash, 'mobileApp': False}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    token = data.get('jwt') or data.get('token')
    if not token:
        raise RuntimeError('Login fehlgeschlagen')

    user = data.get('user', {})
    student = user.get('associatedStudent')
    if not student and user.get('associatedParents'):
        student = user['associatedParents'][0].get('student')
    if not student:
        raise RuntimeError('Kein Schüler im Account gefunden')
    return token, student


def fetch_lessons(email, password, day):
    session = requests.Session()
    token, student = login(session, email, password)
    payload = {
        'bundleVersion': fetch_bundle_version(session),
        'requests': [{
            'moduleName': 'schedules',
            'endpointName': 'get-actual-lessons',
            'parameters': {'student': {'id': student['id']},
                           'start': day.isoformat(), 'end': day.isoformat()},
        }],
    }
    resp = session.post(API_BASE_URL + '/api/calls', json=payload,
                        headers={'Authorization': 'Bearer ' + token}, timeout=30)
    resp.raise_for_status()
    result = resp.json().get('results', [{}])[0]
    if result.get('status') == 'error':
        raise RuntimeError('API-Fehler: ' + str(result.get('message', '')))
    return result.get('data', [])



#
# Normalize lessons
#

def short_time(value):
    # "07:45:00" -> "07:45"
    return value[:5] if isinstance(value, str) else ''


def lesson_details(lesson):
    subject = lesson.get('subject') or {}
    teachers = lesson.get('teachers') or []
    room = lesson.get('room') or {}
    return {
        'subject': subject.get('name') or subject.get('abbreviation') or lesson.get('subjectLabel', ''),
        'teacher': ', '.join(t.get('abbreviation') or t.get('lastname', '') for t in teachers),
        'room': room.get('name', ''),
    }


def normalize(raw_lessons, day):
    rows = []
    for lesson in raw_lessons:
        if lesson.get('date') and lesson['date'] != day.isoformat():
            continue
        lesson_type = lesson.get('type', '')
        cancelled = bool(lesson.get('isCancelled')) or lesson_type == 'cancelledLesson'
        originals = lesson.get('originalLessons') or []
        if cancelled and originals:
            details = lesson_details(originals[0])
        else:
            details = lesson_details(lesson.get('actualLesson') or {})
        class_hour = lesson.get('classHour') or {}
        details.update({
            'number': class_hour.get('number', ''),
            'time': short_time(class_hour.get('from')),
            'until': short_time(class_hour.get('until')),
            'cancelled': cancelled,
            'substitution': lesson_type == 'substitution',
        })
        rows.append(details)

    def sort_key(row):
        try:
            return int(row['number'])
        except (TypeError, ValueError):
            return 99
    return sorted(rows, key=sort_key)



#
# Weather (Open-Meteo, works worldwide without API key)
#

# WMO weather code -> icon id in weather-script-preprocess.svg
WMO_ICONS = {0: 'skc', 1: 'few', 2: 'sct', 3: 'ovc', 45: 'fg', 48: 'fg',
             56: 'fzra', 57: 'fzra', 66: 'fzra', 67: 'fzra',
             80: 'shra', 81: 'shra', 82: 'shra', 85: 'sn', 86: 'sn'}
WMO_ICONS.update({code: 'ra' for code in (51, 53, 55, 61, 63, 65)})
WMO_ICONS.update({code: 'sn' for code in (71, 73, 75, 77)})
WMO_ICONS.update({code: 'tsra' for code in (95, 96, 99)})


def outdoor_hours(rows):
    # From one hour before the first until one hour after the last lesson
    lessons = [row for row in rows if not row['cancelled'] and row['time'] and row['until']]
    if not lessons:
        return 7, 17
    start = min(int(row['time'][:2]) for row in lessons) - 1
    end = max(int(row['until'][:2]) for row in lessons) + 1
    return max(start, 0), min(end, 23)


def fetch_weather(latitude, longitude, day, hours):
    params = {
        'latitude': latitude, 'longitude': longitude,
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min',
        'hourly': 'precipitation_probability',
        'timezone': 'Europe/Berlin',
        'start_date': day.isoformat(), 'end_date': day.isoformat(),
    }
    resp = requests.get(WEATHER_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    daily, hourly = data['daily'], data['hourly']

    start, end = hours
    window = [i for i, t in enumerate(hourly['time']) if start <= int(t[11:13]) <= end]
    probabilities = [hourly['precipitation_probability'][i] or 0 for i in window]
    return {
        'icon': WMO_ICONS.get(daily['weather_code'][0], 'ovc'),
        'high': round(daily['temperature_2m_max'][0]),
        'low': round(daily['temperature_2m_min'][0]),
        'rain_probability': max(probabilities, default=0),
    }


def load_icon(icon_id):
    # Reuse the icon paths of the weather display
    svg = codecs.open(os.path.join(SCRIPT_DIR, 'weather-script-preprocess.svg'), 'r', encoding='utf-8').read()
    match = re.search(r'<path id="%s" [^>]*/>' % re.escape(icon_id), svg)
    return match.group(0) if match else ''


def render_weather(weather, rain_threshold):
    out = [
        '<g transform="translate(462 6) scale(0.58)" fill="white">%s</g>' % load_icon(weather['icon']),
        '<text x="528" y="32" font-size="22" fill="white">%d° / %d°</text>' % (weather['low'], weather['high']),
        '<text x="528" y="58" font-size="17" fill="white">Regen %d %%</text>' % weather['rain_probability'],
    ]
    if weather['rain_probability'] >= rain_threshold:
        out.append('<rect x="664" y="17" width="126" height="36" rx="4" fill="white"/>')
        out.append('<text x="671" y="41" font-size="17" font-weight="bold" textLength="112" lengthAdjust="spacingAndGlyphs">Regenjacke!</text>')
    return '\n'.join(out)




#
# Fish name for fishdraw (made from the cancelled lessons)
#

# Subject -> latin-sounding adjective (matched as prefix of the lowercased subject)
FISH_WORDS = [
    ('mathe', 'mathematicus'), ('deutsch', 'germanicus'), ('englisch', 'anglicus'),
    ('franz', 'gallicus'), ('latein', 'latinus'), ('griech', 'graecus'),
    ('spanisch', 'hispanicus'), ('italien', 'italicus'), ('musik', 'musicus'),
    ('sport', 'athleticus'), ('bio', 'biologicus'), ('chemie', 'chemicus'),
    ('physik', 'physicus'), ('geschichte', 'historicus'), ('erdkunde', 'geographicus'),
    ('geo', 'geographicus'), ('kunst', 'artisticus'), ('ev', 'religiosus'),
    ('kath', 'religiosus'), ('relig', 'religiosus'), ('ethik', 'ethicus'),
    ('informatik', 'informaticus'), ('sozial', 'politicus'), ('politik', 'politicus'),
    ('wirtschaft', 'oeconomicus'), ('natur', 'technicus'), ('philo', 'philosophicus'),
    ('psycho', 'psychologicus'), ('theater', 'theatricus'),
]


def latinize(subject):
    # Fallback for unknown subjects: "Astronomie" -> "astronomicus"
    word = subject.lower()
    for umlaut, replacement in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        word = word.replace(umlaut, replacement)
    word = re.sub(r'[^a-z]', '', word.split()[0] if word.split() else '')
    if not word:
        return ''
    if word.endswith('ik'):
        return word[:-2] + 'icus'
    if word.endswith('ie'):
        return word[:-2] + 'icus'
    if word.endswith('en'):
        word = word[:-2]
    if word[-1] in 'aeiou':
        word = word[:-1]
    return word + 'us'


def fish_word(subject):
    lower = subject.lower().strip()
    for prefix, word in FISH_WORDS:
        if lower.startswith(prefix):
            return word
    return latinize(subject)


def fish_name(rows):
    # "Nonmathematicus musicus biologicus" - at most three names, empty without cancellations
    words = []
    for row in rows:
        word = fish_word(row['subject']) if row['cancelled'] else ''
        if word and word not in words:
            words.append(word)
    if not words:
        return ''
    words = words[:3]
    return 'Non' + words[0] + ''.join(' ' + word for word in words[1:])


#
# Pick the day to display
#

def display_day(now, switch_time):
    hour, minute = [int(x) for x in switch_time.split(':')]
    day = now.date()
    if now.time() >= datetime.time(hour, minute):
        day += datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day



#
# Render SVG
#

def truncate(text, length):
    return text if len(text) <= length else text[:length - 1] + '…'


def render_rows(rows):
    if not rows:
        return '<text x="400" y="330" font-size="48" text-anchor="middle">Kein Unterricht</text>'

    top = 120
    row_height = min(48, 440 // len(rows))
    font_size = min(30, int(row_height * 0.62))
    out = []
    for i, row in enumerate(rows):
        y = top + i * row_height
        baseline = y + row_height // 2 + font_size // 3
        if i % 2 == 1:
            out.append('<rect x="10" y="%d" width="780" height="%d" fill="#e8e8e8"/>' % (y, row_height))
        if row['substitution']:
            out.append('<rect x="10" y="%d" width="8" height="%d" fill="black"/>' % (y, row_height))

        attrs = 'font-size="%d"' % font_size
        if row['cancelled']:
            attrs += ' fill="#777777"'
        elif row['substitution']:
            attrs += ' font-weight="bold"'

        cells = [
            (28, str(row['number'])),
            (80, row['time']),
            (200, truncate(row['subject'], 20)),
            (530, truncate(row['teacher'], 9)),
            (660, truncate(row['room'], 8)),
        ]
        for x, text in cells:
            out.append('<text x="%d" y="%d" %s>%s</text>' % (x, baseline, attrs, escape(text)))
        if row['cancelled']:
            mid = y + row_height // 2
            out.append('<line x1="20" y1="%d" x2="780" y2="%d" stroke="#777777" stroke-width="3"/>' % (mid, mid))
    return '\n'.join(out)


def render_error(message):
    return ('<text x="400" y="300" font-size="36" text-anchor="middle">Stundenplan nicht verfügbar</text>\n'
            '<text x="400" y="350" font-size="20" text-anchor="middle">%s</text>'
            % escape(truncate(message, 70)))


def write_svg(title, body, weather_svg, now):
    output = codecs.open(os.path.join(SCRIPT_DIR, 'schedule-script-preprocess.svg'), 'r', encoding='utf-8').read()
    output = output.replace('DATE_TITLE', escape(title))
    output = output.replace('UPDATED', now.strftime('%d.%m. %H:%M'))
    output = output.replace('LESSON_ROWS', body)
    output = output.replace('WEATHER', weather_svg)
    codecs.open(os.path.join(SCRIPT_DIR, 'schedule-script-output.svg'), 'w', encoding='utf-8').write(output)



#
# Main
#

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dump-json', action='store_true', help='print raw API data')
    parser.add_argument('--from-json', help='render from saved API data instead of fetching')
    parser.add_argument('--date', help='day to display (YYYY-MM-DD)')
    parser.add_argument('--no-weather', action='store_true', help='skip the weather forecast')
    parser.add_argument('--fish-name', action='store_true', help='only print a fish name from the cancelled lessons')
    args = parser.parse_args()

    config = load_config()
    now = datetime.datetime.now()
    if args.date:
        day = datetime.date.fromisoformat(args.date)
    else:
        day = display_day(now, config['switch_time'])
    title = '%s, %s' % (DAYS_OF_WEEK[day.weekday()], day.strftime('%d.%m.%Y'))

    rows = []
    try:
        if args.from_json:
            with open(args.from_json, encoding='utf-8') as f:
                raw = json.load(f)
        else:
            if not config['email'] or not config['password']:
                raise RuntimeError('Zugangsdaten fehlen (schedule-config.ini)')
            raw = fetch_lessons(config['email'], config['password'], day)
        if args.dump_json:
            print(json.dumps(raw, indent=2, ensure_ascii=False))
        rows = normalize(raw, day)
        body = render_rows(rows)
    except Exception as e:
        print('Fehler: %s' % e, file=sys.stderr)
        body = render_error(str(e))

    if args.fish_name:
        print(fish_name(rows))
        return

    # The weather is optional: without it the schedule is still shown
    weather_svg = ''
    if not args.no_weather:
        try:
            weather = fetch_weather(config['latitude'], config['longitude'], day, outdoor_hours(rows))
            weather_svg = render_weather(weather, config['rain_threshold'])
        except Exception as e:
            print('Wetter nicht verfügbar: %s' % e, file=sys.stderr)

    write_svg(title, body, weather_svg, now)


if __name__ == '__main__':
    main()
