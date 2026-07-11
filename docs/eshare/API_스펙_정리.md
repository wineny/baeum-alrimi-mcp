# 공유누리 OPEN API 스펙 정리 (가이드 v2.2 기준, 2026-07-04 분석)

> 원문: `docs/eshare/공유누리_API_가이드_v2.2.pdf` (18p, 행정안전부·조달청·NIA)
> 인증키: `docs/eshare/인증키_신청기록.md` 참조 (6종 승인 대기)

## 킬브랜치 판정 (계획 C′ §3)

| 판정 항목 | 결과 |
|---|---|
| 정적 카탈로그 제공 여부 | ✅ **제공** — 목록/상세 API가 자원 메타데이터(명칭·주소·좌표·요금·용도 등)를 반환. 실시간 예약슬롯 필드 없음 → 빌드타임 스냅샷 아키텍처 적합 |
| 쿼터 의미 | ✅ **calls/day** — 일일 허용 "요청수" 1,000건. numOfRows **Limit 1000**(PDF 기준; 사이트 표기 100과 상이, 프로브에서 실측 필요) → 1콜당 최대 1,000행이면 쿼터 병목 사실상 해소 |
| ≥500건 확보 가능성 | 승인 후 프로브에서 확정 (resultCode·건수 확인) |

## 엔드포인트 5종

| API | URI | 비고 |
|---|---|---|
| 공유자원 목록 | `https://www.eshare.go.kr/eshare-openapi/rsrc/list/{인증키}` | rsrcClsCd로 분류 필터 |
| 분류별 공유자원 목록 | `https://www.eshare.go.kr/eshare-openapi/rsrc/list/{자원분류코드}/{인증키}` | 경로에 분류코드 |
| 공유자원 상세 | `https://www.eshare.go.kr/eshare-openapi/rsrc/detail/{인증키}` | **rsrcNoList 필수** (Array, Limit 100 → 100건 배치) |
| 자원 관리기관 목록 | `https://www.eshare.go.kr/eshare-openapi/agency/list/{인증키}` | |
| 자원분류 목록 | `https://www.eshare.go.kr/eshare-openapi/rsrc/class/list/{인증키}` | 대/중/소분류 계층 |

## 호출 방식 (주의)
- 공식 예제(Java/JS)는 **JSON body를 실어 보내는 방식** — JS 예제는 `$.ajax type:'post', contentType:'application/json'`, Java 예제는 GET 메서드에 StringEntity(JSON) 첨부라는 특이 구조. **프로브 때 POST+JSON body 우선 시도, 실패 시 GET+query 폴백.**
- 공통 요청 파라미터: `pageNo`(기본 1), `numOfRows`(기본 10, Limit 1000), `updBgngYmd`/`updEndYmd`(수정일 범위, YYYYMMDD) → **증분 업데이트 지원**
- 응답: `resultCode`/`resultMsg`(일부 API는 `code`/`description`), `data` Array
- 에러: 200 OK / 400 파라미터 / 401 인증키 상태 확인 필요 / 403 권한 / 500 내부오류

## 자원분류코드 (rsrcClsCd, 6자리)

| 코드 | 분류 |
|---|---|
| 010000 | 문화·숙박 |
| 010100 | 회의실 (소회의실·대회의실) |
| 010200 | 강의실·강당 |
| 010500 | 체육시설 |
| 010700 | 주차장 |
| 020000 | 물품(생활·사무·교통) |
| 030000 | 연구·실험장비 |
| 040000 | 교육·강좌 |

분류코드 구조: 대분류(앞2) + 중분류(중간2) + 소분류(끝2) = rsrcTypeCd1/2/3

## 응답 필드맵

### 목록 API (rsrc/list)
`rsrcNo`(자원번호·PK), `rsrcNm`(명칭), `zip`, `addr`, `daddr`, `lot`(경도), `latl`(위도), `instUrlAddr`(**해당기관 예약 URL**), `imgFileUrlAddr`(이미지)

### 상세 API (rsrc/detail) — recommend/search 자질의 원천
- 식별·분류: `rsrcNo`, `rsrcNm`, `rsrcClsCd`, `rsrcClsNm`, `rsrcInstCd`, `rsrcInstNm`(관리기관명)
- **정적 자질(결정론 커널용)**: `usePsblYn`(사용가능여부), `freeYn`(무료여부), `amt1`/`amt2`(금액), `area`(넓이), `usePrpse`(사용용도), `inqTag`(검색태그), `rsvtNdlsYn`(예약불필요여부)
- 안내: `rsrcIntr`(자원소개), `atpn`(주의사항), `gdsAtrbCn`(상품속성내용)
- 위치: `zip`, `addr`, `daddr`, `lot`, `lat`, `lcInf`, `sggCd`(시구군코드), `lcInsttCd`/`lcInsttNm`(위치기관)
- **예약채널(tool detail 필수 노출)**: `instUrlAddr`(기관 예약 URL), `dtlUrlAddr`(상세링크 URL)
- 상태: `updYmd`(수정일), `delYn`/`delYmd`(삭제), `rsrcAprvYn`/`rsrcAprvYmd`(자원승인), `linkRsrcYn`, `lossYn`
- ⚠️ **수용인원 명시 필드 없음** — `area`(넓이)·`gdsAtrbCn` 파싱으로 근사하거나 recommend 자질에서 party_size 매칭은 area 기반으로 조정 필요

### 기관 API (agency/list)
`instCd`, `allInstNm`, `lowInstNm`, `upInstCd`, `rprsInstCd`, `ablYn`(0 현존/1 폐지)

### 분류 API (rsrc/class/list)
`rsrcClsCd`, `rsrcClscNm`, `upRsrcClsCd`, `rsrcClsDfin`, `rsrcTypeCd1/2/3`, `useYn`

## 계획 반영 사항
1. **스냅샷 아키텍처 확정** — 목록+상세는 정적 카탈로그. `rsvtNdlsYn`·`usePsblYn`은 스냅샷 시점 값으로 표기(실시간 단언 금지, footer 디스클레이머 유지).
2. **적재 파이프라인**: 분류별 목록(rsrcClsCd) → rsrcNo 수집 → 상세 100건 배치 조회. updYmd 범위로 증분 재빌드.
3. **recommend 자질 조정**: capacity(수용인원) 필드 부재 → area·freeYn·amt·sggCd·rsrcClsCd 기반 가중 랭킹으로 설계 수정.
4. **get_resource_detail 예약채널**: `instUrlAddr` + `dtlUrlAddr` 노출 (Architect 관찰 iii 충족).
5. 문의: 공유누리 고객센터 1644-6566 / OPEN API 데이터활용 게시판.
