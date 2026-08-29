#!/usr/bin/env python3
"""인증서 이름·비밀번호만 다시 설정한다 (학년·반·캘린더 설정은 그대로 유지).

비밀번호는 화면에 표시되지 않고, 파일이 아니라
Windows 자격 증명 관리자(keyring)에만 저장된다.

  사용법:  python fix_cert.py
"""
import sys
from getpass import getpass

import keyring

import teacher_config

try:
    cfg = teacher_config.load_config()
except teacher_config.ConfigMissingError as e:
    print(f"❌  {e}")
    sys.exit(1)

print(f"현재 인증서 이름: {cfg['cert_name'] or '(없음)'}")
print("나이스 '인증서 로그인' 창에 뜨는 이름을 그대로 적으세요.")
print("(그대로 두려면 그냥 Enter)")

cert_name = input(f"새 인증서 이름 [{cfg['cert_name']}]: ").strip() or cfg["cert_name"]
if not cert_name:
    print("❌  인증서 이름이 비어 있습니다.")
    sys.exit(1)

cert_password = getpass("인증서 비밀번호 (화면에 표시되지 않습니다): ")
if not cert_password:
    print("❌  비밀번호가 비어 있습니다. 다시 실행해주세요.")
    sys.exit(1)

keyring.set_password(teacher_config.KEYRING_SERVICE, cert_name, cert_password)
cfg["cert_name"] = cert_name
teacher_config.save_config(cfg)

print(f"\n✅  완료. 인증서 이름 -> '{cert_name}'")
print("   비밀번호는 Windows 자격 증명 관리자에 저장되었습니다.")
