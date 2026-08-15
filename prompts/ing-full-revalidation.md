# K-Baby Made `ing` 전수 재조사 실행 프롬프트

아래 내용을 새 Codex 작업의 첫 메시지로 그대로 사용한다. 실행 시점의 데이터가 기준이며, 이 문서에 적힌 예전 숫자를 기준값으로 고정하지 않는다.

---

너는 K-Baby Made의 제품 조사 책임자이자 데이터 검증 엔진이다.

## 목표

현재 canonical DB에서 중복 연결이 아닌 `status="보류"` 제품을 모두 추출하고, 각 ID를 최소 한 번 이상 끝까지 재조사한다. 완료 기준을 충족한 제품은 `포함`, 공식 제외 근거가 확인된 제품은 `제외`로 전환한다. 근거가 부족한 제품은 억지로 완료하지 말고 `보류`를 유지하되, 확인한 사실·남은 필드·시도한 출처·다음 조사 경로를 구조화해 다시 같은 검색만 반복하지 않게 한다.

한 번의 실행 단위는 최대 50개이지만, 이 작업의 완료 조건은 첫 50개 처리가 아니다. 영속 cursor와 ID별 조사 상태를 사용해 시작 시점의 전체 보류 ID를 모두 순회할 때까지 다음 묶음을 계속 처리한다.

## 운영 대상

- 저장소: `pamyo2607/k-baby-made`
- canonical: `data/master-products.json`
- 조사 큐: `data/revalidation-queue.json`
- 실행 상태: `data/continuous-research-state.json`
- Google Sheet ID: `1eXWn2qhdL2iX6nDi60Uov7sotgkoM0veieE2CTdBT8I`
- 운영 URL: `https://k-baby-made.pamyo26.workers.dev/`

GitHub 사용자명과 Workers 서브도메인은 별개다. `pamyo-2607` GitHub 저장소로 푸시하지 않는다. 과거 Worker 주소는 복구 근거일 뿐 현재 배포 대상으로 사용하지 않는다.

## 시작 기준선

수정 전에 다음을 직접 읽고 기록한다.

1. Git HEAD, origin/main, worktree 변경, 진행 중인 GitHub Actions
2. canonical의 raw·unique·duplicate 및 포함·보류·제외 수
3. 모든 보류 ID와 `revalidationMissingFields`
4. Google Sheet의 현재 ID·판정·행 위치
5. 운영 `meta.json`, `health.json`, 정적 자산 SHA와 UI 통계

기존 미커밋 변경, 제품 ID, 원문 판정 사유, 누적 이력, 사용자 입력값을 삭제하거나 초기화하지 않는다.

## 조사 우선순위

완료에 가까운 제품을 먼저 처리하되 cursor로 전체 ID가 결국 선택되게 한다.

1. missing field 1개
2. KC 번호가 있고 Safety Korea 동일 모델 연결만 부족
3. 제조국만 부족
4. 제조사 또는 수입업체만 부족
5. 공식 숫자 월령만 부족
6. 현재 국내 판매만 부족
7. 공식 모델명만 부족
8. 여러 필드 부족
9. 확인일이 오래된 제품

동일 ID를 반복 선택해 뒤쪽 제품이 영구히 굶지 않게 한다. 각 실행은 `selectedIds`, `cursorStart`, `cursorNext`, `attemptedAt`을 남긴다.

## 제품별 성공 기준

`포함`은 다음을 모두 확인한 경우에만 허용한다.

- 현재 구매 가능한 정확한 국내 상품
- 공식적으로 0~35개월 구간이 포함되는 사용 연령
- 완제품 제조국이 대한민국
- 제조사 또는 수입업체
- 제품에 실제 적용되는 안전·위생·화장품·식품접촉·전기 관련 제도
- 정확한 제품 URL과 판정을 지지하는 공식 근거 URL
- KC 적용 제품이면 판매 제품과 같은 모델의 실제 KC 번호, Safety Korea `certDetail` 상세, 인증상태와 동일제품 연결

제품별 법령을 적용한다. 완구·어린이제품에는 해당 KC 제도를 확인하되, 기저귀·물티슈·젖병·이유식기 등 비KC 제품에 어린이제품 KC 번호를 강요하지 않는다.

`제외`는 다음 중 하나를 직접 지지하는 공식 근거가 있을 때만 허용한다.

- 대한민국 완제품 제조 기준 미충족
- 36개월 이상 전용
- 공식 판매 종료·단종 또는 해외 직구 전용
- 프로젝트 범위 밖 제품군

검색 실패, 404 한 곳, 품절 한 곳, 가격비교 중지, 미확인은 제외 근거가 아니다. 이런 경우 `보류`다.

동일 제품 중복이 확인되면 중복 행을 `제외`로 누적하지 않는다. 삭제 전에 canonical과 Google Sheet의 복구 가능한 백업을 만들고, 신뢰할 수 있는 근거 URL·별칭·확인 이력만 유지할 1개 canonical 행에 병합한다. 그 뒤 중복 활성 행을 삭제하고 `삭제 ID → 유지 ID`, 삭제 전후 수, 백업 위치를 별도 감사 이력에 기록한다. 중복 판정만으로 유지 행을 `포함`으로 바꾸지 않는다.

## 근거와 동일제품 규칙

출처 우선순위는 Safety Korea·정부기관 → 제조사/브랜드/수입사 공식 자료 → 공식몰 → 국내 대형 판매처 → 기타 판매처다. 검색 결과 제목, 블로그, 카페, 리뷰, AI 요약만으로 판정하지 않는다.

판매 확인은 공식몰 한 곳의 명확한 구매 가능 상태 또는 서로 독립된 국내 판매처 두 곳을 원칙으로 한다. 정확한 상품 상세가 아니거나 다른 용량·옵션·세트·공통 상세라면 증거로 쓰지 않는다.

KC 연결은 실제 번호와 `/release/certDetail?certNum=...` 상세 URL을 요구한다. 브랜드·제품명·공식 모델명·제조사·외형/품목·파생모델 중 최소 두 가지가 판매 제품과 일치해야 한다. 다른 옵션이나 구성품의 KC 번호를 재사용하지 않는다.

OCR은 제품정보고시·라벨·설명서의 보조 추출에만 쓰며, OCR 단독으로 KC 번호·제조국·월령·업체를 확정하지 않는다.

## 제품별 결과 계약

각 ID마다 다음 구조를 남긴다.

```json
{
  "id": "string",
  "beforeStatus": "보류",
  "decision": "포함|보류|제외",
  "identity": {
    "brand": "string",
    "exactProductName": "string",
    "officialModel": "string",
    "sameProductSignals": ["string"]
  },
  "resolvedFields": {
    "currentSale": "string|boolean|null",
    "officialAge": "string|null",
    "countryOfManufacture": "string|null",
    "manufacturer": "string|null",
    "importer": "string|null",
    "regulatoryRegime": "string|null",
    "kcNumber": "string|null",
    "safetyKoreaSameModel": "string|boolean|null"
  },
  "remainingMissingFields": ["string"],
  "evidence": [
    {"url": "https://...", "sourceType": "official|government|retailer", "supports": ["string"]}
  ],
  "conflicts": ["string"],
  "attemptedSources": ["https://..."],
  "nextResearchRoute": "string|null",
  "reason": "string",
  "confidence": 0.0,
  "checkedAt": "YYYY-MM-DD"
}
```

근거 URL이 해당 주장과 제품 정체성을 실제로 지지하지 않으면 저장하지 않는다. 직접 확인한 사실과 추론을 구분하고, 충돌하는 출처는 숨기지 않는다.

## 데이터 반영

각 묶음은 ID 기반으로 반영한다.

1. 확인된 중복 삭제 절차를 제외하면 원본 row를 보존한 채 확인된 필드만 갱신한다.
2. 상태·missing fields·판정 사유·공식 URL·누적 이력을 함께 갱신한다.
3. 일시적 네트워크 오류나 예상 밖 parser 오류가 나면 해당 row를 실행 전 상태로 복원하고 감사 오류를 기록한다.
4. 확인된 중복은 백업 후 검증 근거를 유지할 1개 행에 병합하고, 중복 활성 행을 삭제한다. 유지 행의 `duplicateOf`는 비우고 `canonicalProductId`는 자기 ID로 맞추며 삭제 매핑은 별도 감사 이력에 보존한다.
5. canonical과 생성 자산 사이에 직접 수작업 복사를 만들지 말고 생성기를 사용한다.
6. Google Sheet는 전체 교체하지 않는다. 쓰기 전 백업과 diff를 만들고 ID로 행을 찾아 Master DB·재검증 대기열·변경 이력을 갱신한다. 확인된 중복 행만 정확한 ID로 삭제하고 readback으로 삭제 ID 0건과 유지 ID 1건을 확인한다.

## 검증과 게시

변경 후 다음이 모두 통과해야만 커밋·Sheet 동기화·배포를 완료한다.

```bash
npm run check
python3 -m py_compile scripts/*.py
python3 scripts/verify_ultra_quality.py
npx wrangler deploy --dry-run
git diff --check
```

수집 원본 수·활성 canonical 수·고유 제품 수·삭제된 중복 수, 상태 합계, 전체 활성 ID, 삭제 매핑, JSON/CSV/embedded payload, SHA, canonical·fallback·Sheet 일치 여부를 검증한다. 중복 정리 뒤 활성 canonical의 `duplicateOf` 행은 0건이어야 한다. push 직전 origin/main과 실행 중 Actions를 다시 확인하고 force-push하지 않는다.

배포 뒤 운영 정적 파일의 HTTP 200과 SHA를 비교하고 Chromium으로 초기 24개 카드, 검색, 포함·보류·제외 필터, 상세, KC/비KC 필드, 공식 링크, console error 0을 검증한다.

## 실행 지표

각 묶음과 전체 전수조사에 다음을 분리해 기록한다.

- 시작 보류 수와 시작 대상 ID 수
- 선택·시도·공식 근거 확인·실패 수
- 보류→포함, 보류→제외, 보류 유지 수
- 필드별 해결 수
- 일시 장애 수와 판정 변경 없이 복원한 수
- 아직 한 번도 조사하지 않은 시작 대상 수
- 다음 cursor
- 최종 raw·unique·duplicate 및 포함·보류·제외 수

`checked`, `changed`, `successful`, `statusTransition`을 같은 뜻으로 쓰지 않는다. 검색 listing을 찾은 것만으로 공식 재검증 성공으로 세지 않는다.

## 중단 조건

상위 출처에서 필수 사실이 충분히 확인되면 불필요한 검색을 멈춘다. 일시 오류는 최대 두 번 의미 있게 재시도하고 그래도 실패하면 row를 보존한 채 다음 ID로 진행한다.

최종 완료 선언은 시작 시점의 모든 보류 ID가 최소 한 번 조사되고, 모든 변경이 검증·Sheet·GitHub·Cloudflare·Chromium에 일치할 때만 한다. 여전히 보류인 제품은 실패로 숨기지 말고 정확한 blocker와 다음 조사 경로를 보고한다.

지금 설명만 하지 말고 기준선 조사부터 실행하라.
