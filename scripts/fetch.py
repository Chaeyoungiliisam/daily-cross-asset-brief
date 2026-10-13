"""데이터 수집: Yahoo Finance(시세), Investing.com(국고채), FRED(미국 거시), 네이버 금융(투자자별 매매동향),
Trading Economics(경제 일정·한국 물가), 한국은행(기준금리), 구글 뉴스(헤드라인).

외부 패키지 없이 파이썬 표준 라이브러리만 씁니다.
어떤 소스가 실패해도 data/ 폴더의 이전 값을 그대로 두고 다음 단계로 넘어갑니다.
"""
import csv, io, json, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

YAHOO = {
    "^GSPC": "S&P 500", "^IXIC": "나스닥", "^DJI": "다우", "^SOX": "필라델피아 반도체",
    "^VIX": "VIX 변동성", "^KS11": "코스피", "^KQ11": "코스닥",
    "^TNX": "미 국채 10년", "^IRX": "미 국채 3개월",
    "DX-Y.NYB": "달러인덱스", "KRW=X": "원/달러 환율",
    "CL=F": "WTI 원유 선물", "GC=F": "금 선물", "BTC=F": "비트코인 선물",
}
INVESTING = {"KR3Y": ("국고채 3년", 29296), "KR10Y": ("국고채 10년", 29292)}
FRED = ["CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "PPIFIS", "UNRATE", "PAYEMS",
        "A191RL1Q225SBEA", "DFEDTARU"]

log = {}


def get(url, headers=None, tries=2, timeout=15):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 재시도 후 기록
            last = e
            time.sleep(1.5)
    raise last


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def fetch_yahoo(history):
    down = 0
    for sym, name in YAHOO.items():
        if down >= 3:
            log[sym] = "FAIL Yahoo 연속 실패로 건너뜀 (이전 값 유지)"
            continue
        try:
            raw = None
            for host in ("query1", "query2"):
                try:
                    raw = get(f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                              f"{urllib.request.quote(sym)}?range=1y&interval=1d")
                    break
                except Exception:  # noqa: BLE001
                    continue
            if raw is None:
                raise RuntimeError("Yahoo 응답 없음")
            res = json.loads(raw)["chart"]["result"][0]
            meta = res["meta"]
            off = meta.get("gmtoffset", 0)
            closes = res["indicators"]["quote"][0]["close"]
            rows = {}
            for ts, c in zip(res["timestamp"], closes):
                if c is None:
                    continue
                d = datetime.fromtimestamp(ts + off, timezone.utc).strftime("%Y-%m-%d")
                rows[d] = round(float(c), 4)
            if len(rows) < 60:
                raise RuntimeError(f"데이터 부족 {len(rows)}개")
            # 마지막 일봉 종가가 비어 오는 경우: 메타의 정규장 가격으로 채움
            if meta.get("regularMarketPrice") and meta.get("regularMarketTime"):
                d = datetime.fromtimestamp(meta["regularMarketTime"] + off, timezone.utc).strftime("%Y-%m-%d")
                if d not in rows and d > max(rows):
                    rows[d] = round(float(meta["regularMarketPrice"]), 4)
            # 이전에 받아 둔 날짜는 유지하고, 새로 받은 값으로 덮어씀
            old = history.get(sym, {"dates": [], "close": []})
            merged = {d: c for d, c in zip(old["dates"], old["close"]) if c is not None}
            merged.update(rows)
            rows = {d: merged[d] for d in sorted(merged)[-300:]}
            dates = sorted(rows)
            history[sym] = {"name": name, "dates": dates, "close": [rows[d] for d in dates]}
            log[sym] = f"ok {dates[-1]} {rows[dates[-1]]}"
            down = 0
        except Exception as e:  # noqa: BLE001
            log[sym] = f"FAIL {e!r}"
            down += 1
        print(f"{sym:>10}: {log[sym]}", flush=True)


ECOS = {"KR3Y": ("국고채 3년", "010200000"), "KR10Y": ("국고채 10년", "010210000")}


def fetch_ecos_bonds(history):
    """한국은행 ECOS 시장금리(일별). 공개 sample 키는 한 번에 10행까지라 최근 2주만 받아 이어 붙입니다."""
    end = datetime.now(KST)
    start = end - timedelta(days=13)
    for key, (name, code) in ECOS.items():
        try:
            raw = get(f"https://ecos.bok.or.kr/api/StatisticSearch/sample/json/kr/1/10/817Y002/D/"
                      f"{start:%Y%m%d}/{end:%Y%m%d}/{code}")
            rows = {f"{r['TIME'][:4]}-{r['TIME'][4:6]}-{r['TIME'][6:]}": round(float(r["DATA_VALUE"]), 3)
                    for r in json.loads(raw)["StatisticSearch"]["row"] if r.get("DATA_VALUE")}
            if not rows:
                raise RuntimeError("빈 응답")
            old = history.get(key, {"dates": [], "close": []})
            merged = dict(zip(old["dates"], old["close"]))
            merged.update(rows)
            dates = sorted(merged)[-300:]
            history[key] = {"name": name, "dates": dates, "close": [merged[d] for d in dates]}
            log[key] = f"ok(ECOS) {dates[-1]} {merged[dates[-1]]}"
        except Exception as e:  # noqa: BLE001
            log[key] = f"FAIL ECOS {e!r}"


def fetch_investing(history):
    if all(str(log.get(k, "")).startswith("ok") for k in INVESTING):
        return  # ECOS로 이미 받았으면 건너뜀
    end = datetime.now(KST).strftime("%Y-%m-%d")
    start = (datetime.now(KST) - timedelta(days=400)).strftime("%Y-%m-%d")
    for key, (name, pid) in INVESTING.items():
        if str(log.get(key, "")).startswith("ok"):
            continue
        try:
            raw = get(f"https://api.investing.com/api/financialdata/historical/{pid}"
                      f"?start-date={start}&end-date={end}&time-frame=Daily&add-missing-rows=false",
                      headers={"domain-id": "www", "Origin": "https://www.investing.com",
                               "Referer": "https://www.investing.com/"})
            data = json.loads(raw)["data"]
            rows = {x["rowDateTimestamp"][:10]: round(float(x["last_closeRaw"]), 3) for x in data}
            if len(rows) < 60:
                raise RuntimeError(f"데이터 부족 {len(rows)}개")
            old = history.get(key, {"dates": [], "close": []})
            merged = dict(zip(old["dates"], old["close"]))
            merged.update(rows)
            dates = sorted(merged)[-300:]
            history[key] = {"name": name, "dates": dates, "close": [merged[d] for d in dates]}
            log[key] = f"ok {dates[-1]} {merged[dates[-1]]}"
        except Exception as e:  # noqa: BLE001
            log[key] = f"FAIL {e!r} (이전 값 유지)"


def fetch_fred():
    out = load("fred.json", {})
    for sid in FRED:
        if any("FRED 차단" in str(log.get(s, "")) for s in FRED):
            log[sid] = "FAIL FRED 차단으로 건너뜀 (경제 캘린더 실제치로 보완)"
            continue
        try:
            txt = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2023-01-01").decode()
            rows = [(r[0], float(r[1])) for r in csv.reader(io.StringIO(txt)) if r and r[0][:1].isdigit() and r[1] not in (".", "")]
            if not rows:
                raise RuntimeError("빈 응답")
            out[sid] = rows
            log[sid] = f"ok {rows[-1][0]} {rows[-1][1]}"
        except Exception as e:  # noqa: BLE001
            log[sid] = f"FAIL FRED 차단 {e!r}"
    save("fred.json", out)


def fetch_flows():
    """네이버 증권 모바일 API: 코스피·코스닥 투자자별 순매수 (억 원)."""
    flows = load("flows.json", {})
    for key, code in (("kospi", "KOSPI"), ("kosdaq", "KOSDAQ")):
        try:
            j = json.loads(get(f"https://m.stock.naver.com/api/index/{code}/trend",
                               headers={"Referer": "https://m.stock.naver.com/"}))
            num = lambda s: int(str(s).replace(",", "").replace("+", ""))
            d = j["bizdate"]
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            flows[key] = {"date": d, "외국인": num(j["foreignValue"]), "개인": num(j["personalValue"]),
                          "기관": num(j["institutionalValue"])}
            flows["date"] = max(flows.get("date", ""), d)
            flows["source"] = "네이버 증권 (투자자별 순매수, 억 원)"
            log["flows_" + key] = f"ok {d} 외국인 {flows[key]['외국인']}"
        except Exception as e:  # noqa: BLE001
            log["flows_" + key] = f"FAIL {e!r} (이전 값 유지)"
    save("flows.json", flows)


# ---------------------------------------------------------------- 웹 자동 수집 (수작업 없음)
TE_KEEP_US = {
    "inflation rate yoy": "미국 CPI (전년비)", "core inflation rate yoy": "미국 근원 CPI (전년비)",
    "inflation rate mom": "미국 CPI (전월비)", "core inflation rate mom": "미국 근원 CPI (전월비)",
    "ppi mom": "미국 PPI (전월비)", "ppi yoy": "미국 PPI (전년비)", "retail sales mom": "미국 소매판매 (전월비)",
    "non farm payrolls": "미국 비농업 고용", "unemployment rate": "미국 실업률",
    "pce price index yoy": "미국 PCE 물가 (전년비)", "core pce price index yoy": "미국 근원 PCE (전년비)",
    "core pce price index mom": "미국 근원 PCE (전월비)", "gdp growth rate qoq adv": "미국 GDP 속보치 (연율)",
    "gdp growth rate qoq 2nd est": "미국 GDP 잠정치 (연율)", "gdp growth rate qoq final": "미국 GDP 확정치 (연율)",
    "fed interest rate decision": "FOMC 금리 결정", "fomc minutes": "FOMC 의사록",
    "ism manufacturing pmi": "미국 ISM 제조업 PMI", "ism services pmi": "미국 ISM 서비스업 PMI",
    "initial jobless claims": "미국 신규 실업수당 청구", "michigan consumer sentiment prel": "미시간 소비자심리 (예비)",
    "jolts job openings": "미국 JOLTS 구인", "industrial production mom": "미국 산업생산 (전월비)",
    "durable goods orders mom": "미국 내구재 주문", "fed chair warsh speech": "파월 후임 연준 의장 연설",
}
TE_KEEP_KR = {
    "inflation rate yoy": "한국 소비자물가 (전년비)", "interest rate decision": "한국은행 금통위 금리 결정",
    "gdp growth rate qoq adv": "한국 GDP 속보치 (전기비)", "gdp growth rate yoy adv": "한국 GDP 속보치 (전년비)",
    "exports yoy": "한국 수출 (전년비)", "balance of trade": "한국 무역수지", "unemployment rate": "한국 실업률",
    "industrial production yoy": "한국 산업생산 (전년비)", "core inflation rate yoy": "한국 근원물가 (전년비)",
}


def _clean(s):
    return re.sub(r"<[^>]+>|&nbsp;|\s+", " ", s or "").strip()


def fetch_te_calendar(country, keep):
    html = get(f"https://tradingeconomics.com/{country}/calendar", headers={"Referer": "https://tradingeconomics.com/"}).decode("utf-8", "replace")
    out = []
    for part in html.split("<tr data-url=")[1:]:
        g = lambda rx: (re.search(rx, part) or [None, None])[1]
        ev = g(r'data-event="([^"]+)"')
        if not ev:
            continue
        ev = ev.strip().lower()
        name = keep.get(ev)
        if not name and "speech" in ev and "chair" in ev:
            name = "연준 의장 연설"
        if not name:
            continue
        date, tm = g(r"class=' (\d{4}-\d{2}-\d{2})'"), (g(r'calendar-date-\d">\s*([^<]+?)\s*<') or "").strip()
        val = lambda i: _clean(g(rf"id='{i}'[^>]*>([\s\S]*?)</(?:span|a)>"))
        when = None
        if date:
            try:
                dt = datetime.strptime(f"{date} {tm}", "%Y-%m-%d %I:%M %p") if tm else datetime.strptime(date, "%Y-%m-%d")
                when = (dt.replace(tzinfo=timezone.utc).astimezone(KST) if tm else dt.replace(tzinfo=KST)).strftime("%Y-%m-%d %H:%M" if tm else "%Y-%m-%d")
            except ValueError:
                when = date
        out.append(dict(country=country, event=ev, name=name, kst=when, ref=_clean(g(r'class="calendar-reference"[^>]*>([\s\S]*?)</span>')),
                        actual=val("actual"), previous=val("previous"), consensus=val("consensus"), forecast=val("forecast")))
    if not out:
        raise RuntimeError("일정 표를 찾지 못함")
    return out


def fetch_calendar():
    cal = load("calendar.json", {"events": []})
    events = []
    for country, keep in (("united-states", TE_KEEP_US), ("south-korea", TE_KEEP_KR)):
        try:
            ev = fetch_te_calendar(country, keep)
            events += ev
            log["calendar_" + country] = f"ok {len(ev)}건"
        except Exception as e:  # noqa: BLE001
            log["calendar_" + country] = f"FAIL {e!r} (이전 일정 유지)"
            events += [x for x in cal.get("events", []) if x.get("country") == country]
    # 이벤트 기록 누적: 실제치가 나온 것은 지난 결과로 보관
    past = {(x["country"], x["event"], x.get("ref", ""), x.get("kst", "")[:10]): x for x in cal.get("history", [])}
    for x in events:
        if x.get("actual"):
            past[(x["country"], x["event"], x.get("ref", ""), (x.get("kst") or "")[:10])] = x
    save("calendar.json", {"events": events, "history": sorted(past.values(), key=lambda x: x.get("kst") or "")[-200:]})


def fetch_bok():
    """한국은행 기준금리 추이 표 (변경일자, 금리)."""
    try:
        html = get("https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643").decode("utf-8", "replace")
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            c = [_clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(c) >= 3 and re.fullmatch(r"\d{4}", c[0]):
                m = re.match(r"(\d{1,2})월\s*(\d{1,2})일", c[1])
                if m:
                    rows.append([f"{c[0]}-{int(m[1]):02d}-{int(m[2]):02d}", float(c[2])])
        if not rows:
            raise RuntimeError("표를 찾지 못함")
        rows.sort()
        save("bok.json", rows)
        log["bok"] = f"ok {rows[-1][0]} {rows[-1][1]}"
    except Exception as e:  # noqa: BLE001
        log["bok"] = f"FAIL {e!r} (이전 값 유지)"


def fetch_kr_cpi():
    """Trading Economics 한국 물가 페이지의 발표 기록을 누적 저장 (월 → 전년비 %)."""
    store = dict(load("kr_cpi.json", {}))
    try:
        html = get("https://tradingeconomics.com/south-korea/inflation-cpi", headers={"Referer": "https://tradingeconomics.com/"}).decode("utf-8", "replace")
        mon = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
        n = 0
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            txt = _clean(tr)
            m = re.match(r"(\d{4})-(\d{2})-(\d{2}).*?Inflation Rate YoY\s+(\w{3})\s+(-?[\d.]+)%", txt)
            if m and f"{m[1]}-{m[2]}-{m[3]}" > datetime.now(KST).strftime("%Y-%m-%d"):
                continue  # 아직 발표 전 (표의 숫자는 이전치)
            if m:
                m = [None, m[1], m[2], m[4], m[5]]
            if m and m[3] in mon:
                y = int(m[1]) - (1 if mon[m[3]] > int(m[2]) else 0)
                store[f"{y}-{mon[m[3]]:02d}"] = float(m[4]); n += 1
        if not n:
            raise RuntimeError("발표 기록을 찾지 못함")
        save("kr_cpi.json", dict(sorted(store.items())))
        log["kr_cpi"] = f"ok {max(store)} {store[max(store)]}"
    except Exception as e:  # noqa: BLE001
        log["kr_cpi"] = f"FAIL {e!r} (이전 값 유지)"


NEWS_Q = {
    "us": ["뉴욕증시 마감", "미 국채 금리 연준"],
    "kr": ["코스피 마감", "국고채 금리 마감"],
    "macro": ["연준 금리 인플레이션", "한국은행 금통위"],
}


def fetch_news():
    """구글 뉴스 RSS 검색 (최근 1일). 제목·매체·링크·시각."""
    news = {}
    for key, qs in NEWS_Q.items():
        items, seen = [], set()
        for q in qs:
            try:
                xml = get("https://news.google.com/rss/search?q=" + urllib.request.quote(q + " when:1d")
                          + "&hl=ko&gl=KR&ceid=KR:ko").decode("utf-8", "replace")
                for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
                    gx = lambda k: (re.search(rf"<{k}[^>]*>(.*?)</{k}>", it, re.S) or [None, ""])[1]
                    title = re.sub(r"\s+-\s+[^-]+$", "", _clean(gx("title")).replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'"))
                    k = re.sub(r"\W", "", title)[:18]
                    if not title or k in seen:
                        continue
                    seen.add(k)
                    items.append(dict(title=title, src=_clean(gx("source")), link=_clean(gx("link")), date=_clean(gx("pubDate"))))
                    if len(items) >= 6:
                        break
            except Exception as e:  # noqa: BLE001
                log[f"news_{key}"] = f"FAIL {e!r}"
        if items:
            news[key] = items
            log[f"news_{key}"] = f"ok {len(items)}건"
    if news:
        old = load("news.json", {})
        old.update(news)
        old["_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        save("news.json", old)


def step(name, fn, *a):
    t0 = time.time()
    try:
        fn(*a)
    except Exception as e:  # noqa: BLE001
        log[name] = f"FAIL {e!r}"
    print(f"[{name}] {time.time() - t0:.1f}초", flush=True)


def main():
    DATA.mkdir(exist_ok=True)
    history = load("history.json", {})
    step("yahoo", fetch_yahoo, history)
    step("ecos", fetch_ecos_bonds, history)
    step("investing", fetch_investing, history)
    save("history.json", history)
    for name, fn in (("fred", fetch_fred), ("flows", fetch_flows), ("calendar", fetch_calendar),
                     ("bok", fetch_bok), ("kr_cpi", fetch_kr_cpi), ("news", fetch_news)):
        step(name, fn)
    log["_fetched_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    save("fetch_log.json", log)
    for k, v in log.items():
        print(f"{k:>18}: {v}")
    fails = [k for k, v in log.items() if str(v).startswith("FAIL")]
    if len(fails) > len(log) // 2:
        print("절반 이상 실패했습니다. 이전 데이터로 페이지를 만듭니다.", file=sys.stderr)


if __name__ == "__main__":
    main()
