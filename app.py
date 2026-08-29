#!/usr/bin/env python3
"""배포판 통합 진입점.

teacher_config.json이 없으면(최초 실행) 설정 마법사를 먼저 실행한 뒤
이어서 출결 자동 입력을 시작한다. 있으면 바로 자동화를 실행한다.
PyInstaller 빌드 시 이 파일을 진입점으로 사용한다.
"""
import asyncio
import sys

import teacher_config

# Windows 콘솔 기본 코드페이지(cp949)는 이모지/일부 한글 조합을 인코딩하지 못해
# print()에서 UnicodeEncodeError로 죽는다. 실행 방식(bat 파일의 PYTHONUTF8 여부,
# exe 직접 더블클릭 등)에 상관없이 항상 동작하도록 여기서 강제로 UTF-8로 전환한다.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main():
    if not teacher_config.CONFIG_FILE.exists():
        import setup_wizard
        setup_wizard.main()
        print("\n▶️  이어서 출결 자동 입력을 시작합니다...\n")

    import main as automation
    asyncio.run(automation.main())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 여기서 안 잡으면 콘솔 창이 오류 내용을 볼 새도 없이 바로 닫혀버린다
        # (exe를 더블클릭으로 바로 실행하면 run.bat의 pause가 없기 때문).
        import traceback
        traceback.print_exc()
        try:
            input("\n❌  예상치 못한 오류로 종료되었습니다. 창을 닫으려면 Enter...")
        except EOFError:
            pass
