"""HTML templates. Rendered by Flask/Jinja with autoescaping on, so every value from a
user, an API or Gemini is escaped before it reaches the page. No |safe anywhere."""

_STYLE = """
<style>
 body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1d2433}
 h1{font-size:1.6rem;margin-bottom:.2rem} .sub{color:#5a6478;margin-top:0}
 label{display:block;font-weight:600;margin-top:1rem} input,select{font-size:1rem;padding:.45rem;width:100%;max-width:360px}
 .err{color:#b00020;font-size:.9rem} .banner{background:#fff4e5;border:1px solid #f0b357;padding:.7rem;border-radius:6px}
 .note{font-size:.85rem;color:#5a6478} button{margin-top:1.2rem;font-size:1rem;padding:.55rem 1.2rem;cursor:pointer}
 table{border-collapse:collapse;width:100%;margin-top:1rem} th,td{border:1px solid #d6dbe4;padding:.5rem;text-align:left;vertical-align:top}
 th{background:#f2f4f8} .src{display:block;font-size:.75rem;color:#6b7487} .advice td{background:#fbfcfe}
 .ok{color:#137333} .pending{color:#a05a00} .failed{color:#b00020}
</style>"""

FORM_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Weather Advisor</title>""" + _STYLE + """</head>
<body>
<h1>Weather Advisor</h1>
<p class="sub">Morning and night forecast, with road, clothing and accessory advice, for any US ZIP code.</p>
{% if banner %}<p class="banner">{{ banner }}</p>{% endif %}
<form method="post" action="/report" novalidate>
  <label for="date">Date (today to 6 days ahead)</label>
  <input id="date" name="date" type="date" min="{{ today }}" value="{{ values.date or today }}" required>
  {% if errors.date %}<div class="err">{{ errors.date }}</div>{% endif %}

  <label for="zip">US ZIP code</label>
  <input id="zip" name="zip" inputmode="numeric" maxlength="5" placeholder="60616" value="{{ values.zip }}" required>
  {% if errors.zip %}<div class="err">{{ errors.zip }}</div>{% endif %}

  <label for="gender">Clothing style</label>
  <select id="gender" name="gender" required>
    {% for key, label in genders %}<option value="{{ key }}" {% if values.gender == key %}selected{% endif %}>{{ label }}</option>{% endfor %}
  </select>
  <div class="note">Used only to tailor the clothing suggestion. It is not stored with your email.</div>
  {% if errors.gender %}<div class="err">{{ errors.gender }}</div>{% endif %}

  <label for="email">Email address (to send the report)</label>
  <input id="email" name="email" type="email" maxlength="254" value="{{ values.email }}" required>
  {% if errors.email %}<div class="err">{{ errors.email }}</div>{% endif %}

  <button type="submit">Get my report</button>
</form>
<p class="note">Privacy: Google Gemini receives only the weather facts and a clothing category
(womenswear, menswear or neutral). Your ZIP code, email and any free text are never sent to it.
Your email is used only to send this report, after you confirm it, and is not written to any log.</p>
</body></html>"""

REPORT_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Weather report</title>""" + _STYLE + """</head>
<body>
<h1>{{ r.facts.place }}, {{ r.facts.state }} {{ r.facts.zip }}</h1>
<p class="sub">{{ r.facts.date }} · local time ({{ r.facts.timezone }})
{% if from_cache %} · saved report{% endif %}
{% if r.stale %} · <strong>weather sources unavailable; showing data {{ r.data_age_minutes }} minutes old</strong>{% endif %}</p>
<table>
 <tr><th></th><th>Morning (06:00–11:59){% if r.partial.morning %}<span class="src">from {{ r.partial.morning }}</span>{% endif %}</th>
     <th>Night (18:00–23:59){% if r.partial.night %}<span class="src">from {{ r.partial.night }}</span>{% endif %}</th></tr>
 {% for row in r.rows %}
 <tr><th>{{ row.label }}</th>
     <td>{{ row.morning }}<span class="src">Source: {{ row.morning_src }}</span></td>
     <td>{{ row.night }}<span class="src">Source: {{ row.night_src }}</span></td></tr>
 {% endfor %}
 <tr class="advice"><th>Suggested road conditions</th><td>{{ r.advice.morning.road }}</td><td>{{ r.advice.night.road }}</td></tr>
 <tr class="advice"><th>Suggested dressing</th><td>{{ r.advice.morning.dressing }}</td><td>{{ r.advice.night.dressing }}</td></tr>
 <tr class="advice"><th>Suggested accessories</th><td>{{ r.advice.morning.accessories }}</td><td>{{ r.advice.night.accessories }}</td></tr>
</table>
<p class="note">Advice: {{ r.advice_source }}.<br>
Weather data: National Weather Service (api.weather.gov); Open-Meteo.com (CC BY 4.0), converted to °F, mph, miles and inches.
Rain and Snow categories are computed by our code from the forecast amount and chance.</p>
<form method="post" action="/email">
  <input type="hidden" name="report_key" value="{{ report_key }}">
  <input type="hidden" name="email" value="{{ email }}">
  <button type="submit">Email this report to {{ email }}</button>
</form>
<p><a href="/">New report</a></p>
</body></html>"""

STATUS_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Email status</title>""" + _STYLE + """</head>
<body>
<h1>Email: <span class="{{ 'ok' if status == 'sent' else status }}">{{ {'sent': 'sent', 'pending': 'waiting for confirmation', 'failed': 'not sent'}[status] }}</span></h1>
<p>{{ message }}</p>
<p><a href="javascript:history.back()">Back to the report</a> · <a href="/">New report</a></p>
</body></html>"""
