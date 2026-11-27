# FarmFinder

초기 조건(`#지역` + `#품목명`)으로부터 **구글 웹·신문기사**, **네이버 뉴스·블로그·지역업체**, **GAP 인증정보 OpenAPI**, **aT FIS 생산자정보**, **행정안전부 도로명주소 OpenAPI**를 **2단계 실시간 꼬리물기(단서 확장·교차검증)** 방식으로 조회하여 대표 작목반·대표 농가와 세부주소·전화번호를 특정하고 **Google Sheets**에 기록하는 웹 애플리케이션입니다.

---

## 핵심 2단계 워크플로

### 1단계 · 지역 대표 작목반 · 대표 농가 발굴 (`POST /api/discover/stream`)
- **입력:** `#지역`(시·군·읍·면) + `#품목명` (+ 선택 관심 조건)
- **동작:** Gemini Function Calling 에이전트가 구글 웹·신문기사(Gemini Search Grounding + Google 뉴스 RSS), 네이버 검색, 국립농산물품질관리원 GAP 인증정보, aT FIS 원료 생산자정보를 실시간 조회하여 해당 지역의 **대표 작목반 · 공선출하회 · 선도농가 · 영농조합법인** 목록을 발굴합니다.
- **활용:** 대표 조직 현황만 필요하면 **[요약 시트 저장]**으로 즉시 기록하고, 세부 주소와 연락처가 궁금한 대상은 **[세부주소 찾기 →]**로 2단계 추적을 시작합니다.

### 2단계 · 세부주소 · 전화번호 꼬리물기 추적 (`POST /api/investigate/stream`)
- **입력:** 1단계에서 선택한 작목반·농가 정보(자동 전달) 또는 좌측 `2단계 · 세부주소 추적` 탭에서 직접 입력한 단서
- **동작:**
  1. 확보된 권역(`~읍/면`), 대표자명, 농장명/법인명을 조합해 GAP OpenAPI 및 aT FIS를 조회하여 **법정리(`~리`)**까지 좁힙니다.
  2. 중간에 새로 포착된 농장명·마을명·대표자명으로 구글 및 네이버 블로그·지역업체를 재검색하고, 유망한 기사·블로그 원문(`m.blog.naver.com` 자동 변환)을 직접 열람해 요약문에 잘린 **세부 번지**와 **전화번호**를 추출합니다.
  3. 후보 주소를 **행정안전부 도로명주소 API**로 검증해 도로명주소·지번주소·우편번호를 최종 확정합니다.
  4. 모든 스텝은 **SSE(Server-Sent Events)**로 실행 즉시 한 단계씩 실시간으로 화면에 스트리밍됩니다.

---

## 설치 및 실행 방법

### 1. 저장소 클론 및 패키지 설치

```bash
git clone https://github.com/OKHand/FarmFinder.git
cd FarmFinder
pip install -r requirements.txt
```

### 2. 환경 변수(`.env`) 설정

```bash
cp .env.example .env
```

`.env` 파일에 직접 입력하거나, 서버 실행 후 우측 상단 **[API · 시트 설정]** 창에서 입력해 저장할 수 있습니다.

| 항목 | 환경 변수명 | 발급처 |
| :--- | :--- | :--- |
| **Gemini API** (추론 + 구글 웹 검색) | `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) |
| **네이버 검색 API** (뉴스·블로그·지역·웹) | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | [네이버 개발자센터](https://developers.naver.com/apps) |
| **GAP 인증정보 OpenAPI** | `DATA_GO_KR_API_KEY` | [공공데이터포털 (15141807)](https://www.data.go.kr/data/15141807/openapi.do) |
| **행안부 도로명주소 API** | `JUSO_API_KEY` | [주소기반산업지원서비스](https://business.juso.go.kr/) |
| **aT FIS 생산자정보** | *(별도 키 불필요)* | `atfis.or.kr/home/producer.do` 실시간 조회 + 선택적 CSV 업로드 |
| **Google Sheets 연동** | `GOOGLE_APPS_SCRIPT_WEBHOOK_URL` 또는 `GOOGLE_SERVICE_ACCOUNT_FILE` | 기본 연결 시트 ID 설정됨 (`GOOGLE_SHEET_ID`) |

### 3. 서버 실행

```bash
python3 run.py
```

브라우저에서 `http://localhost:8000`으로 접속합니다.

---

## Google Sheets 자동 기록 연결 (1분 설정)

1. 상단 **[FarmFinder 시트 열기]** 버튼으로 시트를 엽니다.
2. 시트 메뉴 **확장 프로그램 > Apps Script**를 클릭합니다.
3. 앱 우측 상단 **[API · 시트 설정]** 창에 제공된 `doPost` 스크립트를 붙여넣고 **배포 > 새 배포 > 유형: 웹 앱**(액세스 권한: *모든 사용자*)으로 배포합니다.
4. 생성된 웹 앱 URL을 `GOOGLE_APPS_SCRIPT_WEBHOOK_URL`에 저장하면 이후 버튼 클릭 시 시트에 헤더와 결과 행이 자동 기록됩니다. *(Google Cloud 서비스 계정 JSON 파일 방식도 지원합니다.)*