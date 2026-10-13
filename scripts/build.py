"""페이지 생성: data/ 의 시세·거시 데이터로 이동평균, 매매신호, 브리프 문장을 만들고 docs/index.html 을 씁니다.

선택 기능: 환경변수 ANTHROPIC_API_KEY 가 있으면 Claude API(웹 검색 포함)로
'무슨 일이 있었나' 뉴스 요약을 새로 씁니다. 없으면 규칙 기반 문장만 씁니다.
"""
import json, os, re, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, DOCS = ROOT / "data", ROOT / "docs"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

META = {  # 이름, 그룹, 단위, 소수점, 해석 종류
    "^GSPC": ("S&P 500", "us", "pt", 2, "price"), "^IXIC": ("나스닥", "us", "pt", 2, "price"),
    "^DJI": ("다우", "us", "pt", 2, "price"), "^SOX": ("필라델피아 반도체", "us", "pt", 2, "price"),
    "^VIX": ("VIX 변동성", "us", "pt", 2, "fear"), "^KS11": ("코스피", "kr", "pt", 2, "price"),
    "^KQ11": ("코스닥", "kr", "pt", 2, "price"), "^TNX": ("미 국채 10년", "rate", "%", 3, "rate"),
    "^IRX": ("미 국채 3개월", "rate", "%", 3, "rate"), "KR3Y": ("국고채 3년", "rate", "%", 3, "rate"),
    "KR10Y": ("국고채 10년", "rate", "%", 3, "rate"), "DX-Y.NYB": ("달러인덱스", "fx", "pt", 2, "usd"),
    "KRW=X": ("원/달러 환율", "fx", "원", 2, "usd"), "CL=F": ("WTI 원유 선물", "cmd", "$", 2, "price"),
    "GC=F": ("금 선물", "cmd", "$", 1, "price"), "BTC=F": ("비트코인 선물", "cmd", "$", 0, "price"),
}
US_SESSION = {"^GSPC", "^IXIC", "^DJI", "^SOX", "^VIX", "^TNX", "^IRX", "DX-Y.NYB", "CL=F", "GC=F", "BTC=F"}
PAIRS = [(5, 20, "단기"), (20, 60, "중기"), (60, 120, "장기")]
WD = "월화수목금토일"


def load(name, default=None):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def md(d):
    dt = datetime.strptime(d, "%Y-%m-%d")
    return f"{dt.month}/{dt.day}({WD[dt.weekday()]})"


def sma(v, n):
    return [None if i + 1 < n else round(sum(v[i + 1 - n:i + 1]) / n, 5) for i in range(len(v))]


def crosses(a, b, dates, look=60):
    out, n = [], len(a)
    for i in range(max(1, n - look), n):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        p, q = a[i - 1] - b[i - 1], a[i] - b[i]
        if p <= 0 < q:
            out.append(("golden", dates[i], n - 1 - i))
        elif p >= 0 > q:
            out.append(("dead", dates[i], n - 1 - i))
    return out


# ---------------------------------------------------------------- 시세 → 지표
def session_cutoffs(history):
    """장이 끝나기 전 일봉(장중 값)은 빼고, 마지막으로 끝난 세션까지만 씁니다."""
    from zoneinfo import ZoneInfo
    et = datetime.now(ZoneInfo("America/New_York"))
    us = history["^GSPC"]["dates"]
    us_last = us[-1]
    if us_last == et.strftime("%Y-%m-%d") and (et.hour, et.minute) < (16, 15) and len(us) > 1:
        us_last = us[-2]
    kr = history["^KS11"]["dates"]
    kr_last = kr[-1]
    if kr_last == NOW.strftime("%Y-%m-%d") and (NOW.hour, NOW.minute) < (15, 45) and len(kr) > 1:
        kr_last = kr[-2]
    return us_last, kr_last


KR_SESSION = {"^KS11", "^KQ11", "KR3Y", "KR10Y"}


def indicators(history):
    us_last, kr_last = session_cutoffs(history)
    res = {}
    for k, (name, grp, unit, dp, kind) in META.items():
        h = history.get(k)
        if not h:
            continue
        rows = [(d, c) for d, c in zip(h["dates"], h["close"]) if c is not None
                and not (k in US_SESSION and d > us_last) and not (k in KR_SESSION and d > kr_last)]
        dates, c = [r[0] for r in rows], [r[1] for r in rows]
        if len(c) < 130:
            continue
        m = {n: sma(c, n) for n in (5, 20, 60, 120)}
        last = c[-1]

        def chg(n):
            if len(c) <= n:
                return None
            return (last - c[-1 - n]) if unit == "%" else (last / c[-1 - n] - 1) * 100

        ybase = [x for d, x in rows if d < f"{dates[-1][:4]}-01-01"]
        ytd = None if not ybase else ((last - ybase[-1]) if unit == "%" else (last / ybase[-1] - 1) * 100)
        lm = {n: m[n][-1] for n in m}
        align = "정배열" if lm[5] > lm[20] > lm[60] > lm[120] else "역배열" if lm[5] < lm[20] < lm[60] < lm[120] else "혼조"
        xs = [dict(type=t, date=d, ago=a, pair=f"{s}/{l}", s=s, l=l, label=lab)
              for s, l, lab in PAIRS for t, d, a in crosses(m[s], m[l], dates)]
        xs.sort(key=lambda x: x["date"])
        pc = [dict(type="up" if t == "golden" else "down", date=d, ago=a) for t, d, a in crosses(c, m[20], dates, 10)]
        score = sum([1 if last > lm[20] else -1, 1 if lm[5] > lm[20] else -1,
                     1 if lm[20] > lm[60] else -1, 1 if lm[60] > lm[120] else -1])
        score += sum((1 if x["type"] == "golden" else -1) for x in xs if x["ago"] <= 10 and x["pair"] in ("5/20", "20/60"))
        keep = 260
        res[k] = dict(name=name, grp=grp, unit=unit, dp=dp, kind=kind,
                      dates=dates[-keep:], c=c[-keep:], **{f"ma{n}": m[n][-keep:] for n in m},
                      last=last, d1=chg(1), w1=chg(5), m1=chg(21), m3=chg(63), ytd=ytd,
                      vs20=(last / lm[20] - 1) * 100, vs60=(last / lm[60] - 1) * 100, vs120=(last / lm[120] - 1) * 100,
                      align=align, disp=last / lm[20] * 100, crosses=xs, pcross=pc, score=score,
                      hi52=max(c[-252:]), lo52=min(c[-252:]), asof=dates[-1])
    return res


# ---------------------------------------------------------------- 거시
def macro(fred, manual):
    seed = load("macro_seed.json", {})
    out = {}

    def monthly(sid):
        return {d[:7]: v for d, v in fred.get(sid, [])}

    def yoy(sid):
        s = monthly(sid)
        r = []
        for mth in sorted(s):
            y, mo = int(mth[:4]), mth[5:]
            prev = f"{y - 1}-{mo}"
            if prev in s:
                r.append([mth, round((s[mth] / s[prev] - 1) * 100, 2)])
        return r[-24:]

    for key, sid in (("cpi", "CPIAUCSL"), ("core", "CPILFESL"), ("pce", "PCEPI"), ("cpce", "PCEPILFE"), ("ppi", "PPIFIS")):
        out[key] = yoy(sid) or seed.get(key, [])
    un = sorted(monthly("UNRATE").items())
    out["unrate"] = [[a, b] for a, b in un][-24:] or seed.get("unrate", [])
    pay = sorted(monthly("PAYEMS").items())
    out["nfp"] = [[pay[i][0], int(round(pay[i][1] - pay[i - 1][1]))] for i in range(1, len(pay))][-24:] or seed.get("nfp", [])
    gdp = [[d[:7], v] for d, v in fred.get("A191RL1Q225SBEA", [])]
    out["gdp"] = gdp[-13:] or seed.get("gdp", [])
    ff, prev = [], None
    for d, v in fred.get("DFEDTARU", []):
        if v != prev:
            ff.append([d[:7], v]); prev = v
    out["ffr"] = ff[-8:] or seed.get("ffr", [])
    # FRED가 막혀도 경제 캘린더에 올라온 실제치로 최신 월을 채움
    MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
    CAL_MAP = {("united-states", "inflation rate yoy"): "cpi", ("united-states", "core inflation rate yoy"): "core",
               ("united-states", "pce price index yoy"): "pce", ("united-states", "core pce price index yoy"): "cpce",
               ("united-states", "ppi yoy"): "ppi", ("united-states", "unemployment rate"): "unrate",
               ("united-states", "non farm payrolls"): "nfp", ("united-states", "gdp growth rate qoq adv"): "gdp",
               ("united-states", "gdp growth rate qoq 2nd est"): "gdp", ("united-states", "gdp growth rate qoq final"): "gdp",
               ("united-states", "fed interest rate decision"): "ffr_dec"}
    for e in load("calendar.json", {}).get("history", []):
        key = CAL_MAP.get((e.get("country"), e.get("event")))
        ref, kst, act = (e.get("ref") or "").upper(), e.get("kst") or "", e.get("actual") or ""
        m = re.search(r"-?[\d.]+", act.replace(",", ""))
        if not key or not m or not kst:
            continue
        ry, rm = int(kst[:4]), int(kst[5:7])
        if key == "ffr_dec":
            mm, ref = rm, "DEC"
        elif ref in MON:
            mm = MON[ref]
        elif re.fullmatch(r"Q[1-4]", ref):
            mm = (int(ref[1]) - 1) * 3 + 1
        else:
            continue
        y = ry - (1 if mm > rm else 0)
        mon = f"{y}-{mm:02d}"
        if key == "ffr_dec":  # 금리 결정: 값이 바뀐 경우에만 새 점 추가
            ff = out.get("ffr", [])
            v = float(m.group())
            if not ff or abs(ff[-1][1] - v) > 1e-9:
                out["ffr"] = (ff + [[mon, v]])[-8:]
            continue
        val = float(m.group()) * (1 if key != "nfp" or "K" in act.upper() else 0.001)
        series = dict((a, b) for a, b in out.get(key, []))
        series[mon] = round(val, 2)
        out[key] = [[a, b] for a, b in sorted(series.items())][-24:]
    kc = load("kr_cpi.json", {})
    out["krcpi"] = [[k, v] for k, v in sorted(kc.items())][-24:] or manual.get("kr_cpi", [])
    bok = load("bok.json", [])
    out["bok"] = [[d[:7], v] for d, v in bok][-8:] or manual.get("bok_rate", [])
    return out


def macro_cards(M, manual):
    nx = manual.get("next_release", {})

    def last2(k):
        a = M.get(k) or [[None, None], [None, None]]
        return a[-1], (a[-2] if len(a) > 1 else a[-1])

    def trend(k, n=3):
        a = [x[1] for x in M.get(k, [])][-n - 1:]
        if len(a) < 2:
            return ""
        d = a[-1] - a[0]
        return "최근 석 달 동안 오름세" if d > 0.15 else "최근 석 달 동안 내림세" if d < -0.15 else "최근 석 달 동안 비슷한 수준"

    def infl(k, label, extra):
        (m1, v1), (m0, v0) = last2(k)
        gap = v1 - 2
        txt = f"{label}는 {v1:.2f}%로 전월({v0:.2f}%)보다 {'높아졌습니다' if v1 > v0 else '낮아졌습니다' if v1 < v0 else '같습니다'}. {trend(k)}이고, 연준 목표 2%보다 {abs(gap):.1f}%p {'높습니다' if gap > 0 else '낮습니다'}. {extra}"
        return txt.strip()

    reads = {}
    hc, cc = M["cpi"][-1][1], M["core"][-1][1]
    reads["cpi"] = infl("cpi", "헤드라인 CPI", f"근원과의 격차는 {hc - cc:+.2f}%p로, 이 차이가 클수록 에너지·식품 영향이 크다는 뜻입니다.")
    reads["core"] = infl("core", "근원 CPI", "식품·에너지를 뺀 기조적 물가라 연준이 더 무게를 둡니다.")
    reads["pce"] = infl("pce", "PCE 물가", "연준이 공식 목표로 삼는 물가입니다.")
    reads["cpce"] = infl("cpce", "근원 PCE", "연준이 가장 중시하는 지표입니다.")
    reads["ppi"] = infl("ppi", "PPI", "기업 원가가 몇 달 뒤 소비자 가격으로 옮겨갈 수 있어 CPI의 선행지표로 봅니다.")
    (um, uv), (_, u0) = last2("unrate")
    reads["unrate"] = f"{uv:.1f}%로 전월 대비 {uv - u0:+.1f}%p. {'실업률이 오르면 경기 둔화 신호로 읽힙니다.' if uv > u0 else '고용시장이 아직 버티고 있다는 뜻입니다.'}"
    nf = [x[1] for x in M["nfp"][-3:]]
    reads["nfp"] = f"최근 석 달 평균 {sum(nf) / len(nf):+.0f}천 명. 10만 명 아래면 고용 증가세가 약한 편으로 봅니다." if nf else ""
    (gm, gv), (_, g0) = last2("gdp")
    reads["gdp"] = f"전기 대비 연율 {gv:.1f}% (직전 {g0:.1f}%). {'2%대 이상이면 경기가 버티는 편이라 연준이 물가에 집중할 여유가 있습니다.' if gv >= 2 else '성장이 둔화되면 금리 인상 부담이 커집니다.'}"
    ff = M["ffr"]
    reads["ffr"] = f"현재 상단 {ff[-1][1]:.2f}%. 마지막 변경은 {ff[-1][0][:4]}년 {int(ff[-1][0][5:])}월({(ff[-1][1] - ff[-2][1]) * 100:+.0f}bp)입니다." if len(ff) > 1 else ""
    (km, kv), (_, k0) = last2("krcpi")
    reads["krcpi"] = f"{int(km[5:])}월 {kv:.1f}% (전월 {k0:.1f}%). 한국은행 목표 2%보다 {kv - 2:+.1f}%p입니다."
    b = M["bok"]
    reads["bok"] = f"현재 {b[-1][1]:.2f}%. 마지막 변경 {int(b[-1][0][5:])}월({(b[-1][1] - b[-2][1]) * 100:+.0f}bp)." if len(b) > 1 else ""
    nx = dict(nx, ffr=manual.get("fed_next", ""), krcpi=manual.get("kr_cpi_next", ""), bok=manual.get("bok_next", ""))
    nx.update(next_from_calendar())
    return reads, nx


# ---------------------------------------------------------------- 경제 일정 (자동 수집분)
KEY_EVENTS = {
    ("united-states", "inflation rate yoy"): "cpi", ("united-states", "core inflation rate yoy"): "core",
    ("united-states", "ppi yoy"): "ppi", ("united-states", "ppi mom"): "ppi",
    ("united-states", "pce price index yoy"): "pce", ("united-states", "core pce price index yoy"): "cpce",
    ("united-states", "core pce price index mom"): "cpce", ("united-states", "gdp growth rate qoq adv"): "gdp",
    ("united-states", "gdp growth rate qoq 2nd est"): "gdp", ("united-states", "gdp growth rate qoq final"): "gdp",
    ("united-states", "non farm payrolls"): "nfp", ("united-states", "unemployment rate"): "unrate",
    ("united-states", "fed interest rate decision"): "ffr",
    ("south-korea", "inflation rate yoy"): "krcpi", ("south-korea", "interest rate decision"): "bok",
}
HOT = {"inflation rate yoy", "core inflation rate yoy", "fed interest rate decision", "non farm payrolls",
       "pce price index yoy", "core pce price index yoy", "gdp growth rate qoq adv", "interest rate decision", "ppi mom", "retail sales mom"}


def kst_label(s):
    if not s:
        return ""
    d = datetime.strptime(s[:10], "%Y-%m-%d")
    return f"{d.month}/{d.day}({WD[d.weekday()]})" + (f" {s[11:16]}" if len(s) > 10 else "")


def cal_events():
    return [e for e in load("calendar.json", {}).get("events", []) if e.get("kst")]


def next_from_calendar():
    now = NOW.strftime("%Y-%m-%d %H:%M")
    nx = {}
    for e in sorted(cal_events(), key=lambda e: e["kst"]):
        k = KEY_EVENTS.get((e["country"], e["event"]))
        if k and e["kst"] >= now and k not in nx:
            nx[k] = kst_label(e["kst"]) + (f" · 예상 {e['consensus']}" if e.get("consensus") else "")
    return nx


def calendar_view():
    now = NOW.strftime("%Y-%m-%d %H:%M")
    up, seen = [], set()
    for e in sorted(cal_events(), key=lambda e: e["kst"]):
        if e["kst"] < now or (e["name"], e["kst"][:10]) in seen:
            continue
        seen.add((e["name"], e["kst"][:10]))
        note = " · ".join(x for x in [f"기준 {e['ref']}" if e.get("ref") else "", f"예상 {e['consensus']}" if e.get("consensus") else "", f"이전 {e['previous']}" if e.get("previous") else ""] if x)
        up.append(dict(when=kst_label(e["kst"]), title=e["name"], note=note, hot=e["event"] in HOT, date=e["kst"][:10]))
    return up[:14]


def recent_results(days=4):
    lo = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
    hist = load("calendar.json", {}).get("history", [])
    out = []
    for e in hist:
        if (e.get("kst") or "") < lo or not e.get("actual"):
            continue
        a, c = e["actual"], e.get("consensus", "")
        def num(s):
            m = re.search(r"-?[\d.]+", s.replace(",", "")) if s else None
            return float(m.group()) if m else None
        na, nc = num(a), num(c)
        tag = "" if na is None or nc is None else ("예상 상회" if na > nc else "예상 하회" if na < nc else "예상 부합")
        out.append(dict(when=kst_label(e["kst"]), title=e["name"], actual=a, consensus=c, previous=e.get("previous", ""), tag=tag))
    return out[-8:]


def market_view(D, M, cal, results):
    v = []
    ff = M.get("ffr", [])
    nxt = next((c for c in cal if c["title"] == "FOMC 금리 결정"), None)
    if ff:
        chg = (ff[-1][1] - ff[-2][1]) * 100 if len(ff) > 1 else 0
        v.append(f"<b>연준:</b> 기준금리 상단 {ff[-1][1]:.2f}%. 마지막 결정은 {ff[-1][0][:4]}년 {int(ff[-1][0][5:])}월 {chg:+.0f}bp." + (f" 다음 회의는 {nxt['when']}입니다." if nxt else ""))
    cpi = next((c for c in cal if c["title"].startswith("미국 CPI (전년비)")), None)
    if cpi and "예상" in cpi["note"]:
        v.append(f"<b>다음 미국 CPI ({cpi['when']}):</b> {cpi['note']}. 예상보다 높게 나오면 금리 인상 기대가 커져 국채금리·달러가 오르고 성장주에 부담, 낮으면 반대로 읽힙니다.")
    for r in results[-3:]:
        if r["tag"]:
            v.append(f"<b>발표 결과:</b> {r['title']} {r['actual']} (예상 {r['consensus']}) → {r['tag']}.")
    b = M.get("bok", [])
    kr = M.get("krcpi", [])
    nb = next((c for c in cal if c["title"].startswith("한국은행 금통위")), None)
    if b:
        v.append(f"<b>한국은행:</b> 기준금리 {b[-1][1]:.2f}%" + (f", 최근 물가 {kr[-1][1]:.1f}%({int(kr[-1][0][5:])}월)" if kr else "") + "." + (f" 다음 금통위 {nb['when']}." if nb else ""))
    t, kb = D.get("^TNX"), D.get("KR10Y")
    if t and kb:
        v.append(f"<b>한·미 금리차:</b> 국고채 10년 {kb['last']:.2f}% − 미 국채 10년 {t['last']:.2f}% = {(kb['last'] - t['last']) * 100:+.0f}bp. 미국 금리가 더 높을수록 원화와 외국인 수급에 부담이 됩니다.")
    w = D.get("CL=F")
    if w and w.get("ytd") is not None and abs(w["ytd"]) > 15:
        v.append(f"<b>유가:</b> WTI {w['last']:.1f}달러, 연초 대비 {w['ytd']:+.0f}%. " + ("물가를 밀어 올리는 요인입니다." if w["ytd"] > 0 else "물가 둔화에 도움이 됩니다."))
    return v


# ---------------------------------------------------------------- 브리프 문장
def pct(r):
    return f"{r['d1']:+.2f}%"


def bp(r):
    return f"{r['d1'] * 100:+.1f}bp"


def us_brief(D):
    g, n, dj, sx = D["^GSPC"], D["^IXIC"], D["^DJI"], D["^SOX"]
    t, dx, wti, gold, vix = D["^TNX"], D["DX-Y.NYB"], D["CL=F"], D["GC=F"], D["^VIX"]
    movers = sorted([("S&P 500", g), ("나스닥", n), ("다우", dj), ("반도체", sx)], key=lambda x: -abs(x[1]["d1"]))
    big = movers[0]
    tone = "상승" if g["d1"] > 0.3 else "하락" if g["d1"] < -0.3 else "보합"
    macro_bits = []
    if abs(t["d1"]) >= 0.05:
        macro_bits.append(f"10년물 금리 {'급등' if t['d1'] > 0 else '급락'}")
    if abs(dx["d1"]) >= 0.3:
        macro_bits.append("달러 강세" if dx["d1"] > 0 else "달러 약세")
    if abs(wti["d1"]) >= 1.5:
        macro_bits.append("유가 상승" if wti["d1"] > 0 else "유가 하락")
    lead = f"S&P 500 {tone}({pct(g)}), {big[0]} {pct(big[1])}"
    if macro_bits:
        lead += " · " + ", ".join(macro_bits)
    auto = [
        f"S&P 500 {g['last']:,.2f}({pct(g)}), 나스닥 {n['last']:,.2f}({pct(n)}), 다우 {dj['last']:,.2f}({pct(dj)}).",
        f"필라델피아 반도체 {pct(sx)}. " + ("지수보다 크게 빠져 기술주 약세를 이끌었습니다." if sx["d1"] - g["d1"] < -1 else "지수보다 강하게 올라 기술주 강세를 이끌었습니다." if sx["d1"] - g["d1"] > 1 else "지수와 비슷하게 움직였습니다."),
        f"미 국채 10년 {t['last']:.3f}%({bp(t)}), 3개월물 {D['^IRX']['last']:.3f}%. 장단기 금리차 {(t['last'] - D['^IRX']['last']) * 100:+.0f}bp.",
        f"달러인덱스 {dx['last']:.2f}({pct(dx)}), WTI {wti['last']:.2f}달러({pct(wti)}), 금 {gold['last']:,.1f}달러({pct(gold)}).",
        f"VIX {vix['last']:.2f}({pct(vix)}). " + ("20을 넘어 불안 심리가 큰 편입니다." if vix["last"] >= 20 else "20 아래로 시장 불안은 크지 않은 편입니다."),
    ]
    check = []
    if sx["d1"] <= -1.5:
        check.append("미국 반도체 약세가 삼성전자·SK하이닉스로 번질지")
    elif sx["d1"] >= 1.5:
        check.append("미국 반도체 강세가 국내 반도체 대형주 반등으로 이어질지")
    if dx["d1"] >= 0.3:
        check.append("달러 강세 속 원/달러 환율 상승 압력")
    elif dx["d1"] <= -0.3:
        check.append("달러 약세로 외국인 매수가 들어올지")
    if abs(wti["d1"]) >= 1.5:
        check.append("유가 변동에 따른 정유·항공·화학 업종 차이")
    if abs(t["d1"]) >= 0.05:
        check.append("미국 금리 변동이 국고채 금리와 성장주에 주는 영향")
    if vix["last"] >= 20:
        check.append("위험회피 심리 확대 여부")
    if not check:
        check.append("뚜렷한 해외 변수가 없어 국내 수급과 개별 종목 이슈가 중요")
    return dict(date=g["asof"], lead=lead, auto=auto, check=check,
                kv=["^DJI", "^GSPC", "^IXIC", "^SOX", "^VIX", "^TNX", "DX-Y.NYB", "CL=F", "GC=F", "BTC=F"],
                sig=["^GSPC", "^SOX", "DX-Y.NYB", "GC=F"])


def kr_brief(D, flows):
    k, q, b3, b10, fx = D["^KS11"], D["^KQ11"], D.get("KR3Y"), D.get("KR10Y"), D["KRW=X"]
    tone = "상승" if k["d1"] > 0.3 else "하락" if k["d1"] < -0.3 else "보합"
    lead = f"코스피 {k['last']:,.2f}({pct(k)}) {tone}"
    kf = flows.get("kospi") if flows.get("kospi", {}).get("date") == k["asof"] else None
    if kf:
        who = max(kf, key=lambda w: abs(kf[w]) if w != "date" else -1)
        amt = abs(kf[who])
        won = f"{amt // 10000}조 {amt % 10000:,}억" if amt >= 10000 else f"{amt:,}억"
        lead += f" · {who} {won} {'순매수' if kf[who] > 0 else '순매도'}"
    auto = [f"코스피 {k['last']:,.2f}({pct(k)}), 코스닥 {q['last']:,.2f}({pct(q)})."]
    if kf:
        auto.append(f"코스피 수급: 외국인 {kf['외국인']:+,}억, 개인 {kf['개인']:+,}억, 기관 {kf['기관']:+,}억.")
    if b3 and b10:
        auto.append(f"국고채 3년 {b3['last']:.3f}%({bp(b3)}), 10년 {b10['last']:.3f}%({bp(b10)})" +
                    (" — 주가가 크게 빠진 날 채권으로 돈이 몰렸습니다." if k["d1"] < -1.5 and b10["d1"] < 0 else "."))
    auto.append(f"원/달러 {fx['last']:,.2f}원({pct(fx)}). 연초 대비 {fx['ytd']:+.1f}%" + (" (원화 강세)." if fx["ytd"] < 0 else " (원화 약세)."))
    auto.append(f"코스피 이격도 {k['disp']:.1f}" + (" — 과매도 기준(95)보다 낮아 기술적 반등 여지가 있습니다." if k["disp"] <= 95 else " — 과열 기준(105)을 넘었습니다." if k["disp"] >= 105 else " — 과열·과매도 구간은 아닙니다."))
    return dict(date=k["asof"], lead=lead, auto=auto,
                kv=[x for x in ["^KS11", "^KQ11", "KR3Y", "KR10Y", "KRW=X"] if x in D],
                sig=[x for x in ["^KS11", "^KQ11", "KR10Y"] if x in D])


def questions(D):
    k, g, b10, t, dx, fx, gold = D["^KS11"], D["^GSPC"], D.get("KR10Y"), D["^TNX"], D["DX-Y.NYB"], D["KRW=X"], D["GC=F"]
    qs = []
    if abs(k["d1"] - g["d1"]) >= 1:
        qs.append(f"S&P 500은 {pct(g)}, 코스피는 {pct(k)}였습니다. 두 시장의 차이를 업종 구성(반도체 비중)과 외국인 수급으로 설명해 보세요.")
    else:
        qs.append(f"S&P 500({pct(g)})과 코스피({pct(k)})가 비슷하게 움직였습니다. 두 시장을 함께 움직인 공통 변수는 무엇일까요?")
    if b10:
        qs.append(f"국고채 10년은 {bp(b10)}, 미 국채 10년은 {bp(t)} 움직였습니다. 같은 방향(또는 반대 방향)으로 움직인 이유를 생각해 보세요.")
    qs.append(f"달러인덱스는 연초 대비 {dx['ytd']:+.1f}%, 원/달러는 {fx['ytd']:+.1f}%입니다. 원화만의 요인이 있는지 추측해 보세요.")
    recent = [(kk, x) for kk, r in D.items() for x in r["crosses"] if x["ago"] <= 3]
    if recent:
        kk, x = recent[-1]
        qs.append(f"{D[kk]['name']}에서 {md(x['date'])} {x['pair']}일선 {'골든' if x['type'] == 'golden' else '데드'}크로스가 났습니다. 이 신호를 믿어도 될지, 같은 기간 다른 지표와 비교해 보세요.")
    else:
        qs.append(f"금 선물은 {gold['align']} 상태입니다. 달러·금리와 금값의 관계로 지금 흐름을 설명해 보세요.")
    return qs


# ---------------------------------------------------------------- 선택: Claude API 뉴스 요약
def ai_commentary(D, us, kr, commentary):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return commentary
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    snap = {k: {"name": D[k]["name"], "last": D[k]["last"], "d1": round(D[k]["d1"], 3), "asof": D[k]["asof"]} for k in D}
    prompt = ("너는 한국 대학생을 위한 시장 브리프 작성자다. 웹 검색으로 아래 두 세션의 실제 뉴스 원인을 찾아라.\n"
              f"1) 미국 {us['date']} 뉴욕 마감 2) 한국 {kr['date']} 서울 마감.\n"
              f"시세 데이터(검증된 값, 이 숫자와 다르게 쓰지 말 것): {json.dumps(snap, ensure_ascii=False)}\n"
              "각 세션마다 '무슨 일이 있었나'를 한국어 문장 3~4개로, 종목·원인·이벤트 중심으로 써라. 추측은 쓰지 마라.\n"
              '마지막에 JSON 한 줄만 출력: {"us":{"bullets":[...],"source":"매체명"},"kr":{"bullets":[...],"source":"매체명"}}')
    body = {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]}
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read())
        text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        j = json.loads(text[text.rfind("{\"us\""):].strip().split("\n")[0])
        commentary = {"us": {"date": us["date"], **j["us"]}, "kr": {"date": kr["date"], **j["kr"]}}
        (DATA / "commentary.json").write_text(json.dumps(commentary, ensure_ascii=False, indent=1), "utf-8")
        print("Claude 뉴스 요약 갱신 완료")
    except Exception as e:  # noqa: BLE001
        print("Claude 뉴스 요약 실패, 규칙 기반 문장만 사용:", repr(e))
    return commentary


def friendly_fails(flog):
    names = {"CPIAUCSL": "FRED", "CPILFESL": "FRED", "PCEPI": "FRED", "PCEPILFE": "FRED", "PPIFIS": "FRED",
             "UNRATE": "FRED", "PAYEMS": "FRED", "A191RL1Q225SBEA": "FRED", "DFEDTARU": "FRED"}
    out = []
    for k, v in flog.items():
        if not str(v).startswith("FAIL"):
            continue
        n = names.get(k)
        if n == "FRED":
            n = "FRED(미국 거시지표) → 경제 캘린더 실제치로 보완"
        elif k in ("KR3Y", "KR10Y") and str(flog.get(k, "")).startswith("ok"):
            continue
        n = n or (D_NAMES.get(k, k))
        if n not in out:
            out.append(n)
    return out


D_NAMES = {k: v[0] for k, v in META.items()} | {"flows_kospi": "코스피 매매동향", "flows_kosdaq": "코스닥 매매동향",
           "calendar_united-states": "미국 경제 일정", "calendar_south-korea": "한국 경제 일정", "bok": "한은 기준금리",
           "kr_cpi": "한국 물가", "news_us": "미국 뉴스", "news_kr": "한국 뉴스", "news_macro": "거시 뉴스"}


# ---------------------------------------------------------------- main
def main():
    history = load("history.json")
    manual = json.loads((ROOT / "config" / "manual.json").read_text("utf-8"))
    D = indicators(history)
    M = macro(load("fred.json", {}), manual)
    reads, nexts = macro_cards(M, manual)
    flows = load("flows.json", {}) or {}
    seed = manual.get("flows_seed", {})
    for mk in ("kospi", "kosdaq"):
        if not flows.get(mk) and seed.get(mk):
            flows[mk] = dict(seed[mk], date=seed["date"])
            flows["source"] = seed.get("source", "")
    us, kr = us_brief(D), kr_brief(D, flows)
    commentary = ai_commentary(D, us, kr, load("commentary.json", {}))
    for b, key in ((us, "us"), (kr, "kr")):
        c = commentary.get(key, {})
        b["news"] = c.get("bullets", []) if c.get("date") == b["date"] else []
        b["news_src"] = c.get("source", "") if b["news"] else ""
    today = NOW.strftime("%Y-%m-%d")
    upcoming = calendar_view()
    if not upcoming:  # 자동 수집이 한 번도 안 됐을 때만 예비 일정 사용
        upcoming = [dict(c, when=f"{int(c['date'][5:7])}/{int(c['date'][8:])} {c['time']}".strip()) for c in manual.get("calendar", []) if c["date"] >= today][:10]
    soon = [c for c in upcoming if c["date"] <= (NOW + timedelta(days=2)).strftime("%Y-%m-%d") and c.get("hot")][:4]
    kr["tonight"] = [f"{c['when']}: {c['title']}" + (f" ({c['note']})" if c["note"] else "") for c in soon]
    results = recent_results()
    view = market_view(D, M, upcoming, results)
    news = load("news.json", {})
    for b, key in ((us, "us"), (kr, "kr")):
        b["headlines"] = news.get(key, [])[:5]
    edition = "08:00 미국 마감 업데이트" if NOW.hour < 12 else "16:00 한국 마감 업데이트"
    flog = load("fetch_log.json", {})
    payload = dict(D=D, M=M, READS=reads, NEXT=nexts, US=us, KR=kr, FLOWS=flows, CAL=upcoming,
                   VIEW=view, VIEW_SRC="Trading Economics 경제 일정, FRED, 한국은행 (자동 수집)",
                   VIEW_UPDATED=NOW.strftime("%Y-%m-%d %H:%M"), RESULTS=results,
                   MACRO_NEWS=news.get("macro", [])[:5], Q=questions(D),
                   META=dict(generated=NOW.strftime("%Y-%m-%d %H:%M KST"), edition=edition,
                             us=md(us["date"]), kr=md(kr["date"]),
                             fails=friendly_fails(flog)))
    tpl = (ROOT / "template" / "index.html").read_text("utf-8")
    html = tpl.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, "utf-8")
    (DOCS / ".nojekyll").write_text("", "utf-8")
    print(f"docs/index.html 생성 ({len(html) // 1024} KB) · 미국 {us['date']} · 한국 {kr['date']} · {edition}")


if __name__ == "__main__":
    main()
