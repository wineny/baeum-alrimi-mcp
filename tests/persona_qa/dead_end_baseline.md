# Stage-0 baseline (동결 서버 측정) (≤3턴, INV-DEAD-END)

- 궤적: 45개 (question_bank 31 + seeds 14)
- **dead-end: 6** / MUST 위반(전 턴): 0
- 클래스 분포: {'QBANK': 5, 'MULTI-FILTER': 1}

## dead-end 궤적

### P1-4 · P1 · QBANK · 사유=terminal — “존재하지 않는 강좌 ID 999999 상세 보여줘”
- 턴1 `get_course_detail` `{'course_id': 999999}` → cards=0 ph=0 pred=0 alt=[]

### P1-5 · P1 · QBANK · 사유=terminal — “compare course 1 only”
- 턴1 `compare_courses` `{'course_ids': [1]}` → cards=0 ph=0 pred=0 alt=[]

### P4-3 · P4 · QBANK · 사유=terminal — “이 강좌들 비교해줘 3, 15, 27”
- 턴1 `compare_courses` `{'course_ids': [3, 15, 27]}` → cards=0 ph=0 pred=0 alt=[]

### P5-3 · P5 · QBANK · 사유=stall — “status: open, region: Seoul”
- 턴1 `search_courses` `{'status': ['open'], 'region': 'Seoul'}` → cards=0 ph=0 pred=0 alt=[]
- 턴2 `get_filter_options` `{'region': 'Seoul'}` → cards=0 ph=0 pred=0 alt=[]
- 턴3 `search_courses` `{'region': 'Seoul'}` → cards=0 ph=0 pred=0 alt=[]

### P6-3 · P6 · QBANK · 사유=stall — “필터 옵션 뭐 있어?”
- 턴1 `get_filter_options` `{}` → cards=0 ph=0 pred=0 alt=[]
- 턴2 `get_filter_options` `{}` → cards=0 ph=0 pred=0 alt=[]
- 턴3 `get_filter_options` `{}` → cards=0 ph=0 pred=0 alt=[]

### D13 · P2 · MULTI-FILTER · 사유=stall — “서울 토요일 저녁 무료 코딩 수업”
- 턴1 `search_courses` `{'region': '서울', 'keyword': '코딩', 'free_only': True, 'weekday': ['토'], 'time_range': '저녁'}` → cards=0 ph=0 pred=0 alt=[]
- 턴2 `search_courses` `{'region': '서울', 'keyword': '코딩', 'weekday': ['토'], 'time_range': '저녁'}` → cards=0 ph=0 pred=0 alt=[]
- 턴3 `search_courses` `{'region': '서울', 'keyword': '코딩', 'time_range': '저녁'}` → cards=0 ph=0 pred=0 alt=[]

**미수렴 — 개선 라운드 필요**