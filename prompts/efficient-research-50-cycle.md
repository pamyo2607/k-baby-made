# K-Baby Made 고효율 연구 50개 사이클 실행 프롬프트

아래 구분선 안의 내용을 새 Codex 작업 첫 메시지로 그대로 사용한다. 한 번의 작업에서 `ing` 최대 50개를 재검증하고, 신규 제품은 **모든 완료 기준을 통과해 `포함`으로 승격된 제품 50개**를 하나의 사이클로 만든다.

---

너는 `pamyo2607/k-baby-made`의 제품 조사 책임자이자 배포 검증자다. 설명만 하지 말고 조사, 데이터 반영, 검증, Google Sheet 동기화, GitHub 반영, Cloudflare 배포와 운영 확인까지 끝낸다.

## 이번 작업의 결과

1. canonical의 다음 `보류` 제품을 최대 50개 조사한다.
2. 필수 공식 근거가 모두 확인된 제품만 `포함`, 공식 제외 근거가 확인된 제품만 `제외`로 바꾼다.
3. 신규 제품은 후보 50개가 아니라 **중복이 아니며 전체 검증 후 canonical에 `status="포함"`으로 실제 승격된 제품 50개**를 추가한다. 미완성 신규를 canonical `보류`로 넣어 `ing`를 늘리지 않는다.
4. 모든 변경은 canonical, 생성 자산, Google Sheet, GitHub main, Cloudflare 운영 화면에서 일치해야 한다.

`조사 완료`, `URL 발견`, `checked=true`는 `포함`과 같은 뜻이 아니다. 완료 기준을 충족하도록 부족한 근거를 조사하되, 근거가 끝내 부족하면 `보류`를 유지한다. 숫자를 맞추려고 추정하거나 판정 기준을 낮추지 않는다.

## 먼저 읽을 계약

- `AGENTS.md`
- `prompts/ing-full-revalidation.md`
- `prompts/continuous-new-product-research.md`
- `data/master-products.json`
- `data/revalidation-queue.json`
- `data/deleted-duplicate-tombstones.json`
- `.github/workflows/continuous-product-research.yml`

문서에 적힌 과거 집계는 기준값으로 사용하지 말고 실행 시점의 canonical과 운영 상태에서 다시 계산한다. GitHub 계정은 `pamyo2607`, 운영 URL은 `https://k-baby-made.pamyo26.workers.dev/`다.

## 50개 사이클과 기존 안전 상한

현재 시간당 자동화의 1회 안전 상한은 기존 보류 50개, 신규 후보 30개, 카테고리별 신규 후보 5개다. 이 상한을 우회하거나 검증기를 속이지 않는다.

신규 50개 사이클은 다음처럼 안전 웨이브로 나눈다. `30+20`은 앞선 후보가 모두 통과한 최선의 경우일 뿐이며, 매 웨이브 뒤 실제 승격 수로 남은 수를 다시 계산한다.

```text
cycleTarget = 50 new canonical rows with status=포함
wave 1      = 최대 30개, 카테고리별 최대 5개
wave 2      = min(30, remainingToTarget), 최선의 경우 20개
wave 3+     = 앞 웨이브의 탈락·중복으로 50개를 못 채운 경우에만 계속
```

staging, quarantine, canonical pending, alias 병합, 중복, 범위 밖, 근거 부족 후보는 50개에 포함하지 않는다. `promotedIncludedCanonicalIds`에는 시작 기준선에 없던 활성 ID이면서 canonical에 정확히 한 번 존재하고 `status="포함"`, `revalidationMissingFields=[]`, `duplicateOf=""`인 제품만 넣는다.

## 기준선과 영속 상태

도구 호출 전에 짧게 작업 시작을 알린 뒤 다음을 읽기 전용으로 기록한다.

- local HEAD, origin/main, worktree, 실행 중 Actions
- canonical SHA, 활성 ID 집합, 포함·보류·제외 수
- 시작 시점의 보류 ID 전체와 구조화 missing fields
- staging·quarantine·tombstone의 ID, identity, URL 집합
- Google Sheet의 ID·판정·행 위치
- 운영 meta/health/build/SHA/UI 통계

하나의 `cycleRunId` 아래 다음 상태를 저장하고 재시작 때 이어받는다.

```json
{
  "cycleRunId": "YYYYMMDD-HHMM-new50",
  "baseCommit": "sha",
  "canonicalShaBefore": "sha256",
  "pendingBatchTargetIds": [],
  "pendingAttemptedIds": [],
  "newCycleTarget": 50,
  "promotedIncludedCanonicalIds": [],
  "newRemaining": 50,
  "waveIndex": 0,
  "completedQueries": [],
  "rejectedIdentityKeys": [],
  "networkFailures": []
}
```

이 ledger는 시간당 작업이 덮어쓰는 `data/continuous-research-state.json`에 저장하지 않는다. ING는 `data/campaign-ing-revalidation.json`, 신규는 `data/campaign-new50.json`, 후보 승격 매핑은 `data/new-product-promotion-ledger.json`에 원자적으로 저장한다. cursor는 배열 위치가 아니라 ID를 기준으로 유지한다.

## 가장 효율적인 조사 방식

1. 먼저 로컬 코드로 ID, missing fields, 기존 URL, normalized identity, tombstone alias를 필터·조인·정렬한다. 이런 결정론적 집계에 모델 문맥을 쓰지 않는다.
2. 보류 50개는 missing field와 주 출처 도메인으로 묶어 공유 URL cache의 재사용률을 높인 뒤, 서로 겹치지 않는 3개 lane에 최대 17·17·16개로 배분한다.
3. 신규 조사는 6개 카테고리를 3개 독립 lane으로 나눠 병렬 실행한다. 가용 subagent가 적으면 동일 lane을 순차 실행한다.
4. 각 제품은 저장된 공식 URL과 missing fields부터 확인한다. 이미 해결된 필드를 처음부터 재조사하지 않는다.
5. 검색은 짧고 구별력 있는 exact query 한 번으로 시작한다. 필수 사실이 없을 때만 공식 사이트 또는 독립 판매처 fallback을 추가한다.
6. 상위 공식 근거가 판정을 충분히 지지하면 검색을 멈춘다. 문구를 풍부하게 만들기 위한 추가 검색은 하지 않는다.
7. 일시 장애는 같은 호출 반복이 아니라 URL 직접 접근, 공식 사이트 검색, 독립 판매처 순서로 최대 두 번만 의미 있게 재시도한다.
8. 각 lane은 구조화 결과만 반환하고 canonical 수정은 coordinator 한 곳에서 ID 기반으로 한 번만 적용한다. `outcome ID 집합 == pendingBatchTargetIds`가 아니면 배치 전체를 반영하지 않는다.

같은 ID 또는 normalized identity를 여러 lane에 배정하지 않는다. 독립 읽기는 병렬화하고, 한 결과가 다음 판정을 결정하는 KC 동일 모델 연결과 최종 상태 변경은 순차 검증한다.

## Phase A — `ing` 최대 50개 재검증

시작 시점의 보류 ID를 immutable target으로 고정한다. 다음 50개는 완료 가능성이 높은 순서와 starvation 방지 cursor를 함께 사용한다.

우선순위는 missing field 1개, KC 동일 모델 연결 부족, 제조국, 제조사·수입사, 숫자 월령, 현재 판매, 공식 모델명, 여러 필드, 오래된 확인일 순이다. 단, 이미 시도한 ID가 뒤쪽 ID를 계속 막지 않게 ID cursor를 회전한다.

제품별 판정은 `prompts/ing-full-revalidation.md`의 성공 기준과 결과 JSON 계약을 그대로 따른다. 네트워크 오류나 parser 오류가 난 row는 실행 전 snapshot으로 복원한다.

## Phase B — 신규 canonical 50개 사이클

웨이브 1회는 runner 1회와 정확히 대응한다. 시작할 때 per-run metric을 0으로 초기화하고, 종료할 때 별도 wave audit를 저장한다. 각 웨이브에서 6개 카테고리를 모두 시도한다. 첫 웨이브는 카테고리당 최대 5개로 최대 30개를 staging하고, 둘째부터는 `min(30, remainingToTarget)`만 수집한다.

한 50개 사이클에서 자료가 충분한 경우 각 카테고리 최소 5개를 확보하고, 나머지 20개는 현재 canonical 수가 적거나 공식 근거 완성률이 높은 카테고리에 동적으로 배분한다. 수량 균형보다 exact identity와 검증 성공률이 우선이다.

발견 → staging → sanitation → identity 검증 → 범위 검증 → 공식 근거 검증 → canonical 승격을 분리한다. 필수 근거가 하나라도 남으면 staging에 유지하고 canonical에 넣지 않는다. 승격된 후보는 active staging에서 제거하고 `candidateId → finalCanonicalId`, evidence SHA, wave ID를 promotion ledger에 종결 기록한다.

현재 저장소에 안전한 승격 명령이 없으므로 첫 웨이브 전에 1회 호환성 gate를 구현한다. `scripts/promote_verified_candidates.py` 또는 동등한 단일 명령은 검증된 `DISC-*`를 안정적인 `KBM-YYYYMMDD-NNNN` ID로 매핑하고, 재실행 시 같은 ID를 재사용하며, `sequence=1..N`, `canonicalProductId=id`, `duplicateOf=""`, `status="포함"`, `revalidationMissingFields=[]`를 강제한다. 예상 diff와 SHA를 먼저 출력하고, 전체 validator가 통과할 때만 canonical·staging·ledger를 함께 원자적으로 교체한다. 별도 `scripts/verify_promoted_candidates.py`는 promotion ledger와 canonical·staging·wave audit을 대조해 승격 성공식을 검증한다.

제품별 계약과 승격 기준은 `prompts/continuous-new-product-research.md`를 따른다. 기존 제품의 색상·판매처·단순 수량·포장 변형은 신규가 아니라 alias 또는 기존 evidence로 병합한다.

## 공통 공식 근거 기준

`포함` 또는 신규 `included`는 다음을 모두 직접 지지하는 exact evidence가 필요하다.

- 현재 구매 가능한 정확한 국내 상품
- 공식 0~35개월 사용 구간
- 완제품 제조국 대한민국
- 제조사 또는 수입업체
- 제품별 실제 적용 법령
- 정확한 제품·공식 근거 URL
- KC 적용 제품이면 실제 KC 번호, Safety Korea `certDetail`, 유효 상태와 판매 제품 동일 모델 연결

출처 우선순위는 Safety Korea·정부기관 → 제조사·브랜드·수입사 공식 자료 → 공식몰 → 국내 대형 판매처 → 기타 판매처다. 검색 결과, AI 요약, 블로그, 리뷰, OCR만으로 확정하지 않는다. 비KC 제품에는 어린이제품 KC를 강요하지 않는다.

## 원자적 반영과 검증

각 50개 작업 묶음은 다음 순서를 지킨다.

1. 실행 전 canonical·staging·state·audit SHA와 ID 집합 저장
2. lane별 조사 결과 schema 검증
3. ID·normalized identity·URL·KC 중복 최종 대조
4. 확인된 필드만 canonical에 ID 기반 patch
5. 생성기로 JSON·CSV·embedded·meta·queue 재생성
6. 전체 로컬 gate 통과
7. Google Sheet 백업, ID 기반 동기화, 전 셀 readback
8. Cloudflare 배포와 운영 HTTP/SHA/브라우저 확인
9. proof 갱신과 external validator 통과
10. origin/main과 Actions 재확인 후 조사 데이터+proof를 force-push 없이 하나의 연구 커밋으로 반영
11. lock 해제 제어 커밋을 반영하고 원격 lock 부재를 readback

sanitation·research effectiveness·research target gate는 **promotion 전** staging에 대해 실행한다. 승격 뒤 staging에서 후보를 제거한 다음 같은 sanitizer를 다시 실행하면 탈락으로 오집계되므로, promotion 후에는 ledger-aware promotion validator와 canonical gate만 실행한다.

```bash
# promotion 전
python3 scripts/sanitize_naver_candidates.py
python3 scripts/verify_research_effectiveness.py
python3 scripts/enforce_research_targets.py

# 검증된 후보 promotion 후
python3 scripts/verify_promoted_candidates.py
python3 scripts/enforce_ultra_quality.py
npm run check
python3 -m py_compile scripts/*.py
python3 scripts/verify_ultra_quality.py
npx wrangler deploy --dry-run
git diff --check
```

첫 데이터 변경 전에 validator의 과거 고정값 449·상태·카테고리·queue·build가 정상 상태 전환과 신규 승격을 막는지 확인한다. 막는다면 품질 기준을 낮추지 말고 이를 `실행 전 기준선 + 검증된 batch delta`, 상태합, 연속 sequence, ID 집합, 중복 0, queue 재계산, 동적 build/SHA 불변식으로 바꾸고 회귀 테스트를 먼저 통과시킨다. 자동화 상한 변경은 범위가 아니며 30/카테고리5 웨이브 계약을 유지한다.

수동 캠페인과 시간당 Action이 동시에 쓰지 않도록 첫 실행 전에 공용 lock을 구현한다. workflow가 origin/main의 `data/manual-research-lock.json`을 확인해 활성 lock이면 쓰기 단계를 건너뛰도록 검증한다. 순서는 `lock 획득 제어 커밋 → 조사·Sheet·배포·proof → 조사 데이터+proof 단일 커밋 → lock 해제 제어 커밋`이다. lock에는 `campaignId`, owner, baseSha, createdAt, expiresAt을 기록하며 실행 중 Action 0건과 원격 lock readback 뒤에만 조사한다. TTL 만료 lock은 자동 삭제하지 말고 사람이 원격 상태를 확인한 뒤 해제한다.

신규 성공 gate는 코드로 다음을 강제한다. `len(set(promotedIncludedCanonicalIds)) == 50`; 모든 ID가 기준선에 없고 canonical에 정확히 1건 존재하며 `status="포함"`; tombstone·duplicate·staging active ID가 아님; `afterActive = beforeActive + 50 - deletedExistingDuplicates`; 카테고리·상태·Sheet·생성 자산 수가 동일함. 이 식을 만족하지 않으면 사이클은 미완료다.

## 사이클 완료 보고

다음 값을 수치와 ID로 보고한다.

- ING 선택·시도·포함 전환·제외 전환·보류 유지·오류 복원
- 신규 raw 발견·staging·quarantine·alias·중복·검증 실패
- 신규 canonical 승격 수와 정확한 50개 ID
- 카테고리별 승격 수
- 시작/종료 canonical 및 포함·보류·제외·중복 수
- Sheet cell diff, 운영 build, 자산 SHA, 브라우저 결과
- 다음 pending cursor와 다음 신규 cycle 시작점

신규 승격이 50개 미만이면 완료라고 쓰지 않는다. 동일 query를 반복해 숫자를 채우지 말고 새 공식 경로와 다음 웨이브로 계속한다. 단, 최대 6웨이브 또는 후보 검증 180건에 도달했거나, 연속 3웨이브에서 새 sanitized identity와 승격이 모두 0이면 정지한다. 검증·Sheet·배포 불일치, 원격 동시 변경, credential 부재도 즉시 중단 조건이다. 중단 시 정확한 blocker, 이미 승격된 ID, 남은 수, 재개 체크포인트를 보고한다.

지금 기준선을 기록한 뒤 Phase A부터 실행하라.
