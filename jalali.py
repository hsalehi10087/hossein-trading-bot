"""تبدیل تاریخ میلادی به شمسی در متن گزارش‌ها (بدون وابستگی خارجی).

to_jalali_text(text): هر تاریخ میلادی به شکل 2026-11-27 یا 2026/11/27 (با ارقام لاتین یا فارسی) را به شمسی
(مثلاً 1405/09/06) تبدیل می‌کند؛ رقم‌ها همان سبک (لاتین/فارسی) متن اصلی را حفظ می‌کنند.
"""
import re

_FA = '۰۱۲۳۴۵۶۷۸۹'
_TO_EN = str.maketrans(_FA, '0123456789')
_TO_FA = str.maketrans('0123456789', _FA)
_RE = re.compile(r'(?<![\d۰-۹/\-.])((?:20|۲۰)[\d۰-۹]{2})([-/])([01\d۰-۹]{2})\2([0-3\d۰-۹]{2})(?![\d۰-۹])')


def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def _sub(m):
    y, sep, mo, d = m.group(1), m.group(2), m.group(3), m.group(4)
    fa = any(ch in _FA for ch in m.group(0))
    try:
        gy, gm, gd = int(y.translate(_TO_EN)), int(mo.translate(_TO_EN)), int(d.translate(_TO_EN))
        if not (1 <= gm <= 12 and 1 <= gd <= 31):
            return m.group(0)
        jy, jm, jd = g2j(gy, gm, gd)
    except Exception:
        return m.group(0)
    out = f'{jy}/{jm:02d}/{jd:02d}'
    return out.translate(_TO_FA) if fa else out


def to_jalali_text(text):
    return _RE.sub(_sub, text) if isinstance(text, str) else text


def convert_kw(kw):
    """روی آرگومان‌های requests.post (data/params) برای text و caption و کپشن آلبوم‌ها اعمال می‌شود."""
    import json
    d = kw.get('data')
    if isinstance(d, dict):
        d = dict(d)
        for k in ('text', 'caption'):
            if k in d:
                d[k] = to_jalali_text(d[k])
        if isinstance(d.get('media'), str):
            try:
                media = json.loads(d['media'])
                for it in media:
                    if 'caption' in it:
                        it['caption'] = to_jalali_text(it['caption'])
                d['media'] = json.dumps(media, ensure_ascii=False)
            except Exception:
                pass
        kw = dict(kw, data=d)
    return kw


def jdate(x, short=False, ym=False):
    """'2026-11-25' ← '1405/09/04' (short: '09/04'، ym: '1405/09')؛ اگر قابل‌خواندن نبود همان ورودی."""
    s = str(x)[:10].replace('/', '-')
    try:
        gy, gm, gd = (int(v) for v in s.split('-'))
        jy, jm, jd = g2j(gy, gm, gd)
    except Exception:
        return str(x)
    if short:
        return f'{jm:02d}/{jd:02d}'
    return f'{jy}/{jm:02d}' if ym else f'{jy}/{jm:02d}/{jd:02d}'
