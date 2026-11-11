"""나이스 교육정보 개방 포털(open.neis.go.kr)에서 우리 학교 학사일정을 읽어 휴업일을 찾는다.

키 없이 된다. 다만 키 없이 부르면 한 번에 5줄까지만 돌려주고 쪽 넘기기(pIndex)도 무시된다
(2026-10-16 실측) — 그래서 기간으로 한꺼번에 묻지 않고 «하루씩» 묻는다. 하루에 일정이 5개를 넘는 날은 없다.
휴업일 = 그날 일정 중 수업공제일 구분(SBTR_DD_SC_NM)이 «휴업일» 또는 «공휴일»인 것이 하나라도 있으면.
(재량휴업일 · 방학 · 공휴일 · 토요휴업일이 여기에 든다)
"""
import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

# 학교망이 HTTPS를 교육청 인증서로 다시 감싸면(HTTPS 검사) 파이썬 기본 인증서 목록으로는
# CERTIFICATE_VERIFY_FAILED 가 난다 — 윈도우 인증서 저장소를 쓰게 한다 (2026-11-11 «학교를 찾지 못했습니다» 제보)
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:   # 옛 설치(truststore 없음)는 그대로 돈다
    pass

HUB = "https://open.neis.go.kr/hub/"

# 나이스 주소 앞글자 → 교육청 코드 (17개 모두 2026-10-16 schoolInfo 로 확인)
OFFICE_BY_NEIS = {
    "sen": "B10", "pen": "C10", "dge": "D10", "ice": "E10", "gen": "F10",
    "dje": "G10", "use": "H10", "sje": "I10", "goe": "J10", "gwe": "K10",
    "cbe": "M10", "cne": "N10", "jbe": "P10", "jne": "Q10", "gbe": "R10",
    "gne": "S10", "jje": "T10",
}


TIMEOUT = 15   # 학교망 · EVPN 은 첫 연결이 느릴 때가 있다 (옛 값 8초)


def _kind(e: Exception) -> str:
    """cert · timeout · http · conn · other. URLError 는 속(reason)의 진짜 예외를 타입으로 본다
    (윈도우 인증서 오류 문구는 한국어라 글자로는 못 가른다 — 2026-11-11 코덱스 검토)"""
    if isinstance(e, urllib.error.HTTPError):
        return "http"
    inner = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, BaseException) else e
    if isinstance(inner, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
        return "cert"
    if isinstance(inner, (socket.timeout, TimeoutError)) or "timed out" in str(inner).lower():
        return "timeout"
    if isinstance(inner, (ssl.SSLError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected,
                          urllib.error.URLError, OSError)):
        return "conn"
    return "other"


def _open(req):
    """한 번 실패하면(시간 초과 · 연결 끊김 · 응답이 중간에 잘림) 2초 쉬고 한 번 더. 인증서 · HTTP 오류는 다시 해도 같으니 바로 올린다"""
    for n in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            if n == 2 or _kind(e) not in ("timeout", "conn"):
                raise
            time.sleep(2)


def why(e: Exception) -> str:
    """예외를 선생님이 알아볼 말로 — 설정 마법사가 띄운다"""
    k = _kind(e)
    if k == "cert":
        return "인증서 문제 — 학교망이 HTTPS를 검사하는 것 같습니다. 집 · 휴대폰 핫스팟에서 다시 하거나, 클로드에게 이 줄을 보여 주세요"
    if k == "timeout":
        return "시간 초과 — 나이스 학교 검색 서버가 응답하지 않습니다. 잠시 뒤 다시 하세요"
    if k == "http":
        return f"나이스 서버 오류 (HTTP {e.code}) — 잠시 뒤 다시 하세요"
    if k == "conn":
        return "연결 실패 — 인터넷이 안 되거나 학교망이 open.neis.go.kr 을 막고 있습니다"
    return "알 수 없는 오류"


def _get(service: str, **params) -> list:
    url = HUB + service + "?" + urllib.parse.urlencode({"Type": "json", **params})
    req = urllib.request.Request(url, headers={"User-Agent": "neis-automation-kit"})
    data = json.loads(_open(req).decode("utf-8"))
    block = data.get(service)
    if not block:
        return []   # INFO-200 = 해당 자료 없음
    return block[1].get("row", [])


def office_from_neis_url(neis_url: str) -> str:
    host = urllib.parse.urlsplit(neis_url).netloc.split(".")[0]
    return OFFICE_BY_NEIS.get(host, "")


def find_school(name: str, office: str = "") -> list:
    """학교 이름(일부도 됨)으로 찾는다. [{office, code, name, office_name, addr}]"""
    params = {"SCHUL_NM": name.strip()}
    if office:
        params["ATPT_OFCDC_SC_CODE"] = office
    return [{
        "office": r["ATPT_OFCDC_SC_CODE"], "code": r["SD_SCHUL_CODE"], "name": r["SCHUL_NM"],
        "office_name": r["ATPT_OFCDC_SC_NM"], "addr": (r.get("ORG_RDNMA") or "").strip(),
    } for r in _get("schoolInfo", **params)]


def is_holiday(office: str, code: str, d: date) -> bool:
    rows = _get("SchoolSchedule", ATPT_OFCDC_SC_CODE=office, SD_SCHUL_CODE=code,
                AA_YMD=d.strftime("%Y%m%d"))
    return any(r.get("SBTR_DD_SC_NM") in ("휴업일", "공휴일") for r in rows)


def holidays_among(office: str, code: str, dates) -> set:
    """주어진 날짜들 가운데 우리 학교 휴업일만 골라 돌려준다. 조회가 안 되면 예외를 그대로 올린다."""
    return {d for d in sorted(set(dates)) if d.weekday() < 5 and is_holiday(office, code, d)}
