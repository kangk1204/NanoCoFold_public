# Review fixes — 10 November 2026

Five confirmed findings were corrected without changing prediction settings, scores, rankings or selection thresholds.

- **C01 (P2):** Local ColabFold search reads `query.a3m`, the filename produced from the FASTA header. MMseqs and ColabFold searches use separate attempt directories.
- **C02 (P2):** Automatic searches no longer accept a sequence-only legacy cache as evidence of matching search conditions. Reuse requires a matching request, declared database-manifest digest, executable digest, validated alignment and output digest. Without a declared database revision, a fresh search is run. Explicit `backend: cache` remains supported. Existing cache files and previous attempts are preserved.
- **C03 (P2):** Workspace polling waits for the previous response before scheduling the next refresh. Request generations still reject obsolete responses after navigation or a newer action.
- **C04 (P3):** Native/offline reports use the recorded comparison MSA preparation receipt for comparison observations. Explicit single-sequence and mixed conditions remain distinct. The site applies the same caption to preserved reports without rewriting result data.
- **C05 (P3):** The Korean and English quickstarts now distinguish the default eight-model mature example from the twelve-model example with the WT control included.

## Verification

- Private CPU suite: 5,165 passed; 127 skipped; zero failures.
- Public CPU suite: 1,841 passed; 75 skipped; zero failures.
- Site contracts: 963 passed; 4 skipped; zero failures. Type checking and lint passed.
- New tests cover native search filenames, database/backend/condition identity, corrupt output, interrupted search, concurrent identical requests, recorded MSA captions, delayed polling and stale-response rejection.
- Browser QA with a controlled three-second response delay reached the completed state in both languages. The actual four-engine WT pilot report displayed the recorded MSA source in Korean and English; saved summaries, observations and candidate values were unchanged.

GPU computation was not started or changed for these fixes. The existing 404-model calculation continues with its original source and settings. H200 and A6000 were not used. The private remote CI billing exception remains in effect; local test results are reported separately. Production deployment and download checks are recorded separately in the release validation evidence.

## 한국어 요약

MSA 검색 파일명·캐시 재사용 조건, 느린 응답에서 작업 상태가 갱신되지 않는 문제, 비교 MSA 출처 표시, WT 포함 여부에 따른 예제 모델 수 안내를 수정했습니다. 기존 캐시·분석 결과·선별 기준과 진행 중인 404모델 계산은 유지합니다. 자동 검색의 재사용은 선언된 DB 버전과 실행 조건 및 결과 해시가 일치할 때만 허용합니다. DB 전체 파일을 새로 해시한 검증과는 구분됩니다.
