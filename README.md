# K-Baby Made

대한민국에서 현재 판매되는 0~35개월 영유아 제품 중 완제품 대한민국 제조와 동일 제품 Safety Korea 근거를 확인하는 누적 데이터베이스입니다.

## 복구 상태

- 새 저장소: `pamyo2607/k-baby-made`
- 복구 원본: Google Sheet `K-Baby Made Live DB`
- 복구 가능한 고유 제품: 230개
- 이전 비공개 저장소에서 확인됐던 고유 제품: 455개
- 차이 225개는 임의 생성하지 않고 자동 재조사 대상으로 기록
- 모든 포함 제품은 대한민국 제조와 정확한 KC 번호와 적합 상태와 Safety Korea 개별 상세를 요구

## 검증

```bash
python -m pip install -r requirements.txt
python scripts/bootstrap_from_sheet.py
python scripts/build_assets.py
python scripts/validate_data.py
```

## 배포

Cloudflare Workers Builds에서 저장소 루트 기준으로 배포합니다.

```bash
npx wrangler deploy
```

`wrangler.jsonc`의 `assets.directory`가 `./public`으로 설정되어 있어 별도 Build command는 필요하지 않습니다.

## 자동 조사

`.github/workflows/continuous-product-research.yml`

- 매시간 보류 제품 최대 50개 재검증
- 카테고리별 신규 후보 최대 30개 조사
- 최초 트리거는 6개 카테고리를 모두 조사
- 검색 결과만으로 포함 처리하지 않음
- 엄격 검증과 데이터 빌드가 통과한 경우에만 결과 커밋
