# 카카오 '나에게 보내기' 리프레시 토큰 재발급 가이드

> 이 저장소는 공개다. 토큰·키 값은 이 문서, 채팅, 커밋 어디에도 적지 않는다. (`.env` 에만 저장)

## 언제 필요한가
- 야간 파이프라인의 크레딧 경고·공지 알림이 카톡으로 안 올 때. 로그에 아래 문구가 보이면 이 문서대로 재발급한다.
  - `카카오 경고 알림 발송 실패: 액세스 토큰 갱신 실패 - HTTP 400 invalid_grant / KOE322 / expired_or_invalid_refresh_token`
- 원인: 리프레시 토큰은 유효기간이 있고(재발급 때 화면에 일수가 표시된다), 로컬 파이프라인의 `send_alert()` 는 새로 내려온 토큰을 저장하지 않는다. 그래서 기간이 지나면 조용히 만료된다. (2026-10-21 22:45 발송 실패가 이 경우)

## 준비물
- `.env` 에 `KAKAO_REST_API_KEY`, `KAKAO_REDIRECT_URI` 가 있어야 한다. Client Secret 사용을 켰다면 `KAKAO_CLIENT_SECRET` 도.
- 카카오 개발자 콘솔 > 내 애플리케이션 > 카카오 로그인 > 동의항목 에서 **카카오톡 메시지 전송(talk_message)** 이 켜져 있어야 한다.
- 파이프라인이 도는 시간(00:00~)에는 `.env` 를 바꾸지 않는 편이 안전하다. 실행 중인 프로세스는 시작 때 읽은 값을 쓴다.

## 절차 (프로젝트 폴더에서, 터미널은 PowerShell)
1. 동의 화면 주소 만들기
   ```powershell
   $env:PYTHONUTF8 = 1
   C:/Users/user1/Desktop/클로드/.venv/Scripts/python.exe -m generator.send_to_me --auth-url
   ```
   출력된 주소를 브라우저에 붙여넣는다.
2. 본인 카카오 계정으로 로그인하고 **'카카오톡 메시지 전송'** 에 동의한다.
3. 이동된 주소창의 `?code=` 뒤 값을 복사한다. (1회용, 약 10분 안에 사용)
4. 토큰 발급
   ```powershell
   C:/Users/user1/Desktop/클로드/.venv/Scripts/python.exe -m generator.send_to_me --exchange <복사한_code>
   ```
   화면에 `KAKAO_REFRESH_TOKEN=...` 과 `리프레시 토큰 유효기간: 약 N일` 이 출력되고, `talk_message 동의 확인: OK` 여야 한다. 실패 시 `--auth-url` 로 코드를 다시 받는다.
5. 출력된 값을 `.env` 의 `KAKAO_REFRESH_TOKEN=` 에 붙여넣어 저장한다. (GitHub Actions 로 공지를 보낸다면 Repository secret `KAKAO_REFRESH_TOKEN` 도 같은 값으로 교체)
6. 확인(내 카톡 '나와의 채팅'으로 테스트 메시지 1통이 간다)
   ```powershell
   C:/Users/user1/Desktop/클로드/.venv/Scripts/python.exe -c "from generator.send_to_me import send_alert; send_alert('[Buyg] 카카오 토큰 재발급 테스트')"
   ```
   오류 없이 끝나고 카톡이 오면 완료.

## 다음 만료 대비
- 4번에서 표시된 유효기간(일)을 기준으로, 만료 5~7일 전 날짜에 캘린더 알림을 걸어 둔다.
- 개선 제안(미적용): 발급일을 기록해 두고 만료 임박 시 파이프라인이 로그에 한 줄 경고를 남기도록 한다. 승인 후 진행.

## 자주 나오는 오류
| 메시지 | 뜻 | 조치 |
|---|---|---|
| `KOE322` / `expired_or_invalid_refresh_token` | 리프레시 토큰 만료 또는 잘못된 값 | 위 절차로 재발급 |
| `토큰 발급 실패 ... 인가 코드는 1회용` | code 재사용·10분 초과 | `--auth-url` 부터 다시 |
| `talk_message 동의 확인: 누락` | 동의항목 미설정 | 콘솔 동의항목 켜고 2번부터 다시 |
| `KAKAO_REST_API_KEY / KAKAO_REFRESH_TOKEN 미설정` | `.env` 값 없음 | `.env` 확인 |
