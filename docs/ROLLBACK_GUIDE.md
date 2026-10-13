# 롤백 가이드 (ROLLBACK_GUIDE)

> 작업을 잘못 적용했을 때 이전 상태로 되돌리는 운영 매뉴얼. 기준 문서는 `PROJECT_CONTEXT.md`(7장 8번 규칙).
> ⚠️ 이 저장소는 공개(GitHub Pages)다. 백업 폴더(`backups/`)에는 DB·데이터가 들어 있어 **커밋 금지**(`.gitignore` 처리됨).

## 0. 먼저 알아둘 것

### 자동 백업 원칙
- 클로드는 코드/데이터를 **수정하기 직전마다** 사용자 요청이 없어도 `python tools/backup_snapshot.py --note "작업 설명"` 을 먼저 실행한다. 백업이 실패하면 작업을 시작하지 않는다.
- 스냅샷은 `backups/snapshot_YYYYMMDD_HHMMSS/` 에 저장되고 **최근 20개만** 유지된다(오래된 것부터 자동 삭제). 같은 초에 두 번 만들면 `_2` 가 붙는다.
- 각 스냅샷에는 `manifest.json`(생성 시각, 설명, 파일 목록·크기·해시)이 있다.

### 스냅샷에 들어가는 파일
| 구분 | 파일 |
|---|---|
| 데이터 | `data/gonggu.db`(SQLite 백업 API로 일관성 있게 복사), `data/targets.json`, `free_spots_seed.json` |
| 화면 | `index.html`, `map.html`, `generator/templates/view_page.html` |
| 산출물 JSON | `tour_events.json`, `seoul_reserve.json`, `kids_performances.json`, `search_index.json` |

### 스냅샷에 들어가지 않는 것 (다른 방법으로 되돌린다)
- **코드**(`generator/`, `collector/`, `parser/`, `tools/` 등): 방법 C(git)로 되돌린다.
- 인스타 로그인 세션·`.env`·`data/raw_collected.json`·Cloudinary 이미지: 백업 대상이 아니다.
- 스냅샷 **이후** 새로 생긴 파일은 롤백해도 지워지지 않는다.

### 롤백 전 체크
1. **야간 파이프라인이 실행 중이 아닌지 확인**(보통 00:00~21:00). 스케줄러 State 가 `Running` 이면 롤백하지 않는다 — 스크립트도 경고 후 중단한다.
2. 롤백 직전에 현재 상태가 **안전 스냅샷**으로 자동 저장된다(롤백을 다시 되돌릴 수 있다).
3. 롤백한 파일은 아직 **배포되지 않았다**. 라이브(buyg.kr)에 반영하려면 커밋·푸시가 필요하다(아래 "배포까지 되돌리기").

---

## 방법 A. 클로드에게 말로 요청하기 (가장 쉬움)
클로드 대화창에 아래처럼 쓰면 된다. 클로드가 목록을 먼저 보여 주고, 복원 전 확인을 받는다.

| 상황 | 요청 예시 |
|---|---|
| 방금 작업 취소 | `방금 작업 취소해줘. 직전 스냅샷으로 롤백해줘.` |
| 목록 확인 | `백업 스냅샷 목록 보여줘.` |
| 특정 시점 | `10/13 11시쯤 스냅샷으로 되돌려줘.` / `snapshot_20261013_112352 로 롤백해줘.` |
| 몇 단계 전 | `스냅샷 3단계 전으로 되돌려줘.` (목록에서 위에서 N번째를 골라 확인 후 복원) |
| 계획만 보기 | `롤백하면 어떤 파일이 바뀌는지만 보여줘(dry-run).` |
| 배포까지 | `롤백하고 라이브에도 반영해줘(커밋·푸시).` |

> 클로드가 할 일: 파이프라인 실행 여부 확인 → `--list` → 대상 확정(모호하면 사용자에게 묻기) → `--dry-run` 으로 계획 보고 → 사용자 확인 후 `--target ... --yes`.

---

## 방법 B. 스크립트 직접 실행
프로젝트 폴더에서(`C:\Users\user1\Desktop\클로드\인스타공구캘린더`) venv 파이썬으로 실행한다.

```bash
# 파이썬: C:/Users/user1/Desktop/클로드/.venv/Scripts/python.exe  (아래에서는 python 으로 줄여 씀)

# 1) 백업 목록 (폴더명 · 시각 · 설명 · 파일 수 · 용량)
python tools/rollback_snapshot.py --list

# 2) 무엇이 복원되는지만 보기 (아무것도 바꾸지 않음)
python tools/rollback_snapshot.py --target snapshot_20261013_112352 --dry-run

# 3) 복원 (계획을 보여주고 y/N 확인)
python tools/rollback_snapshot.py --target snapshot_20261013_112352

# 4) 확인 없이 복원 / 가장 최근 스냅샷으로
python tools/rollback_snapshot.py --target snapshot_20261013_112352 --yes
python tools/rollback_snapshot.py --target latest --yes
```

| 옵션 | 설명 |
|---|---|
| `--list` | 스냅샷 목록 출력(기본 동작) |
| `--target <폴더명>` / `latest` | 복원할 스냅샷 |
| `--dry-run` | 복원 대상만 출력 |
| `--yes` | 확인 질문 생략 |
| `--no-safety` | 복원 직전 안전 스냅샷 생략(권장하지 않음) |

수동으로 지금 상태를 저장하고 싶을 때:
```bash
python tools/backup_snapshot.py --note "큰 작업 전"
```

### 복원 후 확인
- `index.html` 이 템플릿과 어긋났으면 `python tools/rebuild_site_outputs.py` 로 DB 기준 재생성(파이프라인 실행 중에는 금지).
- 브라우저로 홈·지도 화면을 확인한 뒤, 라이브에 반영하려면 커밋·푸시.

---

## 방법 C. Git 커밋 단위로 되돌리기 (코드까지)
코드나 이미 푸시된 변경을 되돌릴 때 쓴다. 스냅샷은 코드를 포함하지 않는다.

```bash
git log --oneline -10          # 되돌아갈 커밋 확인 (예: b4d865e)
git status                     # 커밋 안 된 변경이 있는지 확인
```

### C-1. 안전한 방법: `git revert` (기록을 남기고 되돌림, 공개 저장소에 권장)
```bash
git revert b4d865e            # 해당 커밋의 변경을 취소하는 새 커밋을 만든다
git revert HEAD~2..HEAD       # 최근 3개 커밋을 순서대로 취소
git push origin main          # 일반 푸시(강제 푸시 불필요)
```

### C-2. 다단계 되돌리기: `git reset --hard` (기록 자체를 되돌림 - 주의)
```bash
git branch backup_before_reset      # 먼저 현재 위치에 안전 브랜치를 만들어 둔다
git reset --hard HEAD~3             # 3단계 전 커밋으로 이동 (또는 git reset --hard <커밋해시>)
git push --force-with-lease origin main   # 원격도 맞추려면 강제 푸시가 필요하다
```
- ⚠️ `reset --hard` 는 **커밋 안 된 변경을 전부 지운다**. 먼저 `python tools/backup_snapshot.py --note "reset 전"` 을 실행한다.
- ⚠️ 강제 푸시는 원격 기록을 덮어쓴다. 야간 파이프라인이 돌고 있거나 곧 돌 때(자정 직전)는 피한다 — 파이프라인의 `git push` 가 충돌한다.
- 잘못 reset 했으면 `git reflog` 에서 이전 해시를 찾아 `git reset --hard <해시>` 로 복구할 수 있다.

### C-3. 특정 파일만 이전 커밋 상태로
```bash
git checkout <커밋해시> -- map.html      # map.html 만 그 커밋 상태로
git commit -m "map.html 롤백"
```

---

## 배포까지 되돌리기 (라이브 buyg.kr)
1. 방법 A/B/C 로 작업 폴더를 원하는 상태로 만든다.
2. `python -m unittest discover -s tests` 로 테스트 통과 확인.
3. 로컬 서버(`python -m http.server 8770`)로 홈·지도 화면 확인.
4. 커밋 후 `git push origin main` → GitHub Pages 반영(수십 초~수 분).
5. 야간 파이프라인이 오늘 밤 다시 돌면 DB 기준으로 `index.html` 이 새로 생성되므로, **DB 를 되돌렸다면** 스냅샷 복원 시 DB 도 같이 복원됐는지 `--list` 의 파일 목록으로 확인한다.

## 자주 묻는 질문
- **스냅샷이 너무 쌓이나요?** 최근 20개만 유지된다(스냅샷당 약 40MB → 최대 약 0.9GB).
- **스냅샷을 직접 지워도 되나요?** 된다. `backups/` 아래 `snapshot_*` 폴더를 삭제하면 목록에서 사라진다. 다른 이름의 폴더는 자동 정리 대상이 아니다.
- **롤백했는데 화면이 그대로예요.** 브라우저/서비스워커 캐시일 수 있다. 강력 새로고침(Ctrl+F5) 후 확인한다. 라이브는 푸시 전까지 바뀌지 않는다.
- **파이프라인이 실행 중인데 급히 되돌려야 해요.** 스크립트는 `--yes` 가 있어야만 진행한다. 되도록 파이프라인 종료 후(보통 21시경) 실행하고, 급하면 `--dry-run` 으로 영향부터 확인한다.
