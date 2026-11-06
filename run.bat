@echo off
chcp 65001 > nul
echo ===================================================
echo [뉴스 브리핑 자동 수집 및 AI 요약 시작]
echo ===================================================
python briefing.py
if %ERRORLEVEL% EQU 0 (
    echo.
    echo 브리핑 완료! 브라우저에서 결과를 엽니다...
    start index.html
) else (
    echo.
    echo 실행 중 오류가 발생했습니다.
    pause
)

