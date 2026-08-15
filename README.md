# K-Baby Made

대한민국에서 현재 판매되는 0~35개월 영유아 제품을 제품 단위로 검증하는 누적 데이터베이스와 정적 웹앱입니다. 공식 근거가 부족하면 `보류`, 공식 제외 근거가 있으면 `제외`, 현재 판매·월령·완제품 제조국·제조사/수입사·적용 법령과 해당 시 KC 동일 모델까지 확인되면 `포함`으로 관리합니다.

## 현재 검증 상태

- 운영 build: `20260815-dedup449-final1`
- 활성 레코드 449 / 고유 제품 449 / 활성 중복 0 / 삭제 이력 10
- 고유 판정: 포함 21 / 보류 382 / 제외 46
- UI `❗ 기준 제외`: 46
- 보수적으로 확인된 현재 판매: 210
- 전수 재검증 대상: 403 = 포함 21 + 보류 382
- canonical JSON 파일 SHA-256: `969644332c25fb2adac1b0163dbf478d6335441f11cc07ed6b1473cc1df2c56d`
- compact payload SHA-256: `1fe816db20b85846ccc4449a5281728b0cb11718ad29632a008e3a0831c8edde`
- verified CSV SHA-256: `41646e8e4e8ba538328317cca99ff0f4bc0eb7d0b3364bea487288d70f7fe76e`

동일 제품으로 확인된 중복 행은 canonical과 Google Sheet를 백업한 뒤, 검증된 근거를 유지할 1개 행에 병합하고 활성 데이터에서 삭제합니다. 삭제 ID와 유지 ID의 매핑은 복구용 감사 이력으로 보존하며, 중복 판정만으로 유지 행을 `포함`으로 승격하지 않습니다.

### 2026-08-15 중복 삭제 이력

| 삭제 ID | 유지 canonical ID |
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

Google Sheet `K-Baby Made Live DB`도 활성 449행·38열로 동기화됐습니다. 확정 중복 10행은 삭제 전 전체 사본을 만든 뒤 `Master DB`와 `__strict_sync`에서 제거했고, queue의 Master행 참조도 382건 전부 다시 계산했습니다. 백업·삭제 매핑·전 셀 readback 결과는 `data/google-sheet-sync-proof.json`과 `data/duplicate-deletion-proof.json`에 기록돼 있습니다.

## 로컬 검증

```bash
npm install
npm run check
python3 scripts/verify_ultra_quality.py
npx wrangler deploy --dry-run
```

`npm run check`는 모든 생성물을 다시 만든 뒤 canonical/JSON/CSV/embedded payload/중복/상태/필수 근거/SHA/build 및 JavaScript 문법을 fail-closed로 검증합니다.

Google Sheet와 운영 배포를 확인한 최종 릴리스에서는 `npm run verify:external`로 외부 readback proof까지 별도로 검증합니다. 시간당 자동 조사는 외부 동기화가 끝나기 전의 로컬 자산 검증 때문에 정상 연구 결과를 버리지 않도록 `npm run check`만 실행합니다.

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

- [`prompts/efficient-research-50-cycle.md`](prompts/efficient-research-50-cycle.md): `ing` 최대 50개 재검증과 신규 canonical 승격 50개를 한 작업에서 조정하는 복사·실행용 통합 프롬프트
- [`prompts/ing-full-revalidation.md`](prompts/ing-full-revalidation.md): 시작 시점의 모든 `보류` ID를 immutable ID 집합으로 고정하고, 50개 배치를 반복해 공식 근거가 충족된 행만 `포함` 또는 `제외`로 전환하는 전수 재조사 프롬프트
- [`prompts/continuous-new-product-research.md`](prompts/continuous-new-product-research.md): 신규 `포함` canonical 50개를 campaign 목표로 삼고, 웨이브당 최대 30개를 반복해 staging·identity·중복·범위·공식 근거·승격을 검증하는 지속 조사 프롬프트

모든 프롬프트는 현재 데이터에서 숫자를 다시 계산하며, 완료 수를 늘리기 위해 판정 기준을 낮추지 않습니다. 시간당 GitHub Actions의 한 실행은 기존대로 신규 후보 30개·카테고리당 5개를 staging하는 fail-closed 계약입니다. 수동 신규 50개 campaign은 이 상한을 우회하지 않고 여러 웨이브를 조정하며, canonical에 실제 승격된 고유 ID만 50개에 포함합니다.

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
- `data/duplicate-deletion-proof.json`: 삭제 10건·유지 9건·백업·활성 중복 0 검증
- `data/codex-production-verification.json`: 운영 배포·브라우저 최종 proof

운영 규칙은 `AGENTS.md`를 따릅니다. credential, token, cookie, `.env`는 저장소나 proof에 기록하지 않습니다.
