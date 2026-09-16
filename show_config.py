#!/usr/bin/env python3
"""저장된 설정을 «가려서» 보여준다.

설정 파일을 cat 으로 통째로 찍으면 연락처·인증서 이름이 클로드 대화에 그대로 남는다
(2026-09-16 리허설에서 실제로 새어 나갔다). 확인은 이 스크립트로 한다.

    python show_config.py
"""
import sys

import keyring

import teacher_config


def mask_phone(v: str) -> str:
    """010-1234-5678 → 010-****-5678"""
    parts = v.split("-")
    if len(parts) == 3:
        return f"{parts[0]}-{'*' * len(parts[1])}-{parts[2]}"
    return v[:3] + "*" * max(0, len(v) - 6) + v[-3:] if len(v) > 6 else "***"


def mask_name(v: str) -> str:
    """123홍길동456 → 123홍**456 (앞뒤만 남긴다)"""
    if len(v) <= 4:
        return v[0] + "*" * (len(v) - 1)
    return v[:4] + "*" * (len(v) - 7) + v[-3:] if len(v) > 7 else v[:2] + "*" * (len(v) - 2)


def main() -> int:
    try:
        cfg = teacher_config.load_config()
    except teacher_config.ConfigMissingError as e:
        print(f"❌  {e}")
        return 1

    print("📋  저장된 설정 (개인정보는 가려서 보여줍니다)")
    print(f"    파일: {teacher_config.CONFIG_FILE}")
    print(f"    나이스 주소   : {cfg.get('neis_url', '')}")
    print(f"    학년 / 반     : {cfg.get('grade', '')}학년 {cfg.get('class', '')}반")
    print(f"    인증서 이름   : {mask_name(cfg.get('cert_name', '')) or '(없음)'}")
    contact = cfg.get("contact", "")
    print(f"    연락처        : {mask_phone(contact) if contact else '(안 넣음)'}")
    print(f"    개인결재선    : {'설정됨 ✅' if cfg.get('approval_line') else '(안 넣음)'}")
    print(f"    휴업일 시트   : {'설정됨 ✅' if cfg.get('holiday_sheet_id') else '(안 넣음)'}")
    print(f"    출결 캘린더   : {len(cfg.get('calendars', {}))}개")

    pw = keyring.get_password(teacher_config.KEYRING_SERVICE, cfg.get("cert_name", ""))
    print(f"    인증서 비밀번호: {'저장됨 ✅ (Windows 자격 증명 관리자)' if pw else '❌ 없음 — 설정 마법사를 다시 돌리세요'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
