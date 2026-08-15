# K-Baby Made

대한민국에서 현재 판매되는 0~35개월 영유아 제품을 제품 단위로 검증하는 누적 데이터베이스와 정적 웹앱입니다. 공식 근거가 부족하면 `보류`, 공식 제외 근거가 있으면 `제외`, 현재 판매·월령·완제품 제조국·제조사/수입사·적용 법령과 해당 시 KC 동일 모델까지 확인되면 `포함`으로 관리합니다.

## 현재 검증 상태

- 운영 build: `20260815-live459-recovery2`
- 원본 레코드 459 / 고유 제품 449 / 구조화 중복 10
- 고유 판정: 포함 21 / 보류 382 / 제외 46
- UI `❗ 기준 제외·중복`: 56 = 고유 제외 46 + 중복 연결 10
- 보수적으로 확인된 현재 판매: 210
- 전수 재검증 대상: 403 = 포함 21 + 보류 382
- canonical JSON 파일 SHA-256: `0b2338245935229bf67dc03aa8fc5589384a22f236923b54dce9e027a7afa677`
- compact payload SHA-256: `726d045d6baf95cc0046385291183bbef0237bf4fe272710721846ee8475b767`
- verified CSV SHA-256: `69323c173831886271bdd8083efc7e1c0f83c75ca8bb292d4d9553fbe80e075d`

중복 행은 삭제하지 않습니다.

| 중복 ID | canonical ID |
| --- | --- |
| `TOY-20260729-115` | `TOY-20260729-024` |
| `TOY-20260729-124` | `TOY-20260729-123` |
| `TOY-20260729-125` | `TOY-20260729-053` |
| `TOY-20260729-155` | `TOY-20260729-154` |
| `TEETHER-20260801-015` | `RUN18-008` |
| `RUN18-011` | `MASTER-0195` |
| `TEETHER-20260801-005` | `MASTER-0195` |
| `RUN18-013` | `MASTER-0196` |
| `RUN18-012` | `MASTER-0197` |
| `TEETHER-20260801-026` | `MASTER-0135` |

## 데이터 흐름

`data/master-products.json`이 저장소 canonical입니다. `scripts/build_assets.py`가 다음 배포·감사 자산을 한 번에 생성합니다.

- `fallback-products.json` 및 `data/`, `public/` 사본
- gzip-base64 내장 `kbaby-data.js`
- 38열 `data/master-db-419-final.csv`
- 운영 CSV `public/data/master-db-419-final.csv`
- Google Sheet 동기화용 `public/master-db-sync.csv`
- `meta.json`, `health.json`, missing-fields, queue, summary, proof

웹앱은 검증 CSV를 우선 사용하고 내장 fallback을 장애 복구용으로 사용합니다. Live snapshot은 raw 수, 전체 ID 집합, 중복 맵과 unique 수를 모두 통과해야 연결 성공으로 인정합니다. 과거 파일명 `master-db-419-final.csv`는 하위호환 때문에 유지되지만 현재 재검증 대상은 403입니다.

Google Sheet `K-Baby Made Live DB`도 459행·38열로 동기화됐습니다. 기존 230행의 ID 위치와 누적 이력은 보존하고 누락 229행을 추가했습니다. 쓰기 전 백업과 검증 결과는 `data/google-sheet-sync-proof.json`에 기록돼 있습니다.

## 로컬 검증

```bash
npm install
npm run check
python3 scripts/verify_ultra_quality.py
npx wrangler deploy --dry-run
```

`npm run check`는 모든 생성물을 다시 만든 뒤 canonical/JSON/CSV/embedded payload/중복/상태/필수 근거/SHA/build 및 JavaScript 문법을 fail-closed로 검증합니다.

`scripts/recover_live_canonical.py`는 2026-08-15 복구용 일회성 도구입니다. 고정된 과거 운영 payload와 CSV SHA를 검증하므로 정상 빌드나 시간당 자동 조사에서 실행하지 않습니다.

## 자동 조사

`.github/workflows/continuous-product-research.yml`은 매시간 17분에 한 번 실행되며 동일 실행의 중복을 concurrency lock으로 막습니다.

- 기존 보류 제품 최대 50개
- 신규 후보 전체 최대 30개, 6개 제품군별 최대 5개
- 판매 listing 확인과 공식 재검증 성공을 별도 지표로 기록
- 신규 후보는 canonical에 바로 넣지 않고 `data/discovered-candidate-staging.json`에 격리
- exact product identity와 공식 근거가 확인되기 전 canonical 승격 금지
- 법령별 KC 적용, 네트워크 오류 시 보류 유지
- 품질 gate, 전체 `npm run check`, 최종 검증이 모두 통과한 뒤에만 단일 커밋

## 운영 프롬프트

- [`prompts/ing-full-revalidation.md`](prompts/ing-full-revalidation.md): 실행 시작 시점의 모든 `보류` ID를 누락 없이 순회하고, 공식 근거가 충족된 행만 `포함` 또는 `제외`로 전환하는 전수 재조사 프롬프트
- [`prompts/continuous-new-product-research.md`](prompts/continuous-new-product-research.md): 신규 후보를 실행당 전체 30개·카테고리당 5개 이내로 staging하고, exact identity·중복·범위·공식 근거 검증 뒤에만 canonical로 승격하는 지속 조사 프롬프트

두 프롬프트 모두 현재 데이터에서 숫자를 다시 계산하며, 완료 수를 늘리기 위해 판정 기준을 낮추지 않습니다. 시간당 GitHub Actions는 동일한 상한과 fail-closed 규칙을 코드로 집행합니다.

## Cloudflare 배포

이 프로젝트는 Cloudflare Workers Static Assets입니다. `wrangler.jsonc`의 `assets.directory`는 `./public`이고, `/`와 `/index.html`을 직접 제공하도록 설정돼 있습니다.

```bash
npm run check
npx wrangler deploy --dry-run
npx wrangler deploy
```

배포 뒤에는 `/`, `/index.html`, `/app.js`, `/kbaby-data.js`, 운영 CSV, `/health.json`의 HTTP 상태와 SHA를 저장소 파일과 비교하고 Chromium으로 초기 24개 카드, 검색·카테고리·판정 필터, 제품 상세, KC 필드와 공식 링크를 확인합니다.

## 감사 자료

- `data/codex-revalidation-summary.json`: 시작 기준선, 누적 42건 재검증, 전환·필드 확인 수, 최종 집계
- `data/live-recovery-report.json`: 고정 운영 payload 복구와 120개 오염 후보 격리
- `data/missing-fields-report.json`: 382개 보류 제품의 구조화 missing fields
- `data/google-sheet-sync-proof.json`: Sheet 백업·ID 기반 동기화·readback
- `data/codex-production-verification.json`: 운영 배포·브라우저 최종 proof

운영 규칙은 `AGENTS.md`를 따릅니다. credential, token, cookie, `.env`는 저장소나 proof에 기록하지 않습니다.
