# Music Analysis Studio · MAS

UnivDash 디자인을 바탕으로 만든 한국어 **프로젝트 기반 음악 스튜디오**. FastAPI + Jinja2 + 일반 JavaScript + NumPy/librosa, uv 기반 Python 환경, MongoDB/OpenSSH 연결을 사용한다. 원본 음악을 보컬·악기로 분리하고, 분리된 보컬을 분석한다. 악기의 연주 분석은 포함하지 않는다.

## 구현 기능

- 프로젝트 생성·이름 변경·저장·다시 열기·삭제
- 프로젝트당 WAV/MP3/FLAC **원본 음악 최대 3개**, 상단 음원 탭 전환
- 실제 Demucs 분리: 기본 4스템(보컬·드럼·베이스·기타 반주), 선택적 실험적 6스템(기타·피아노 추가)
- 선택 음원의 실제 파형과 분리 트랙 믹서: Mute/Solo·개별 음량·원본 모니터·재생·탐색·속도·구간 반복
- 음원별 보컬 분석 켜기/끄기, 일반 모드 요약과 고급 모드 그래프·스펙트로그램·프로젝트 보컬 비교
- 키/속도를 독립적으로 바꾼 보컬 변형본과 재분석 (원본 3개 제한과 별개)
- pYIN F0/음역, RMS/다이내믹, 주파수 중심/평탄도, 온셋/BPM 후보, 비브라토 후보, STFT 스펙트로그램
- 측정 리포트와 선택적 LLM 평가, Markdown 제목·표·목록·코드 블록, 근거 JSON 및 최근 리포트 10개
- 배경 작업 진행률, 동일 PCM 보컬 분석 캐시, 실패 표시/재시도, 원본 삭제 시 하위 트랙 정리
- UnivDash의 오로라·메시·애플 글래스·미니멀 테마, 라이트/다크, 접이식 사이드바, 설정 모달, 모바일 메뉴

가짜 측정값이나 데모 녹음은 기본 UI에 넣지 않는다. `검증용 · 합성 신호` 프로젝트가 로컬에 있다면 개발 중 생성한 합성 테스트 음원이며 사람의 노래가 아니다.

## 실행 (Windows PowerShell)

필요: Python 3.11–3.13 (검증: 3.12), uv. Node.js와 npm/pnpm, 프론트 빌드는 필요 없다.

```powershell
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run python run.py
```

브라우저: **http://127.0.0.1:8000** · API 문서: http://127.0.0.1:8000/docs

`uv` 캐시 권한 오류가 있는 제한 환경에서는 `$env:UV_CACHE_DIR = "$PWD/.cache/uv"`를 설정한다. 일반 실행에서는 필요 없다. 서버는 로컬 사용자 전용이며 단일 프로세스로 실행해야 한다.

개발 시에도 `uv run uvicorn backend.app.main:app --reload` 하나만 실행한다. Jinja 템플릿은 서버에서 렌더링하고 CSS/JavaScript/폰트는 `/static`에서 제공한다. 첫 음원 분리 시 공식 Demucs 가중치를 `storage/models/`에 다운로드한다(기본 htdemucs 약 80MB). 네트워크 연결과 저장 공간이 필요하며 이후 재사용한다. CPU 분리는 곡 길이에 따라 수 분 이상 걸릴 수 있다. `MAS_SEPARATION_DEVICE=auto`는 CUDA 사용 가능 시 GPU, 그 외 CPU를 선택한다. 6스템 모델의 피아노·기타 분리는 실험적이다.

첫 pYIN 분석은 numba 초기 컴파일로 1분 이상 걸릴 수 있고, 이후에는 캐시를 재사용한다.

## MongoDB / SSH

`.env`에 기존 SSH Host 별칭과 MongoDB 정보를 입력한다. 비밀번호와 API 키는 Git에 포함하지 않는다.

```dotenv
MAS_SSH_ALIAS=my-server
MAS_SSH_REMOTE_HOST=127.0.0.1
MAS_SSH_REMOTE_PORT=27017
MAS_SSH_LOCAL_PORT=27018
MAS_MONGO_URI=mongodb://USER:PASSWORD@127.0.0.1:27018/?authSource=admin&directConnection=true
MAS_MONGO_DATABASE=music_analysis_studio
```

`my-server`는 기존 `%USERPROFILE%/.ssh/config`의 `Host`다. 앱은 시스템 OpenSSH로 로컬 127.0.0.1 터널을 열고 종료 시 닫는다. 키/에이전트 인증과 이미 신뢰된 호스트 키가 필요하다. 암호 대화창이나 호스트 키 자동 수락은 하지 않는다. 사용자명·비밀번호의 URI 특수문자는 percent-encoding한다. SSH 포트는 해당 Host 설정을 따른다.

이미 직접 터널을 실행한다면 `MAS_SSH_ALIAS`를 비우고 그 로컬 포트의 URI를 지정할 수 있다. SSH 터널을 거치는 단일 Mongo 노드는 `directConnection=true`를 사용한다. [공식 연결 문서](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-targets/)

접속 정보가 없으면 **로컬 미리보기(JSON 저장)**로 명시 표시한다. MongoDB URI가 설정된 상태에서 연결이 실패하면 오류를 반환하며 로컬 저장소로 자동 전환하지 않는다. JSON 미리보기 프로젝트를 MongoDB로 자동 이전하지 않으므로 DB 설정 후 새 프로젝트를 만들거나 별도 이전이 필요하다.

프로젝트/요약/리포트는 MongoDB, 음원과 큰 분석 JSON은 `storage/`에 보관한다. 다른 PC에서 같은 MongoDB를 사용해도 로컬 오디오가 자동 복제되지는 않는다.

## AI 평가

`.env`에 OpenAI 호환 Chat Completions **전체 URL**, 모델, 필요 시 API 키를 설정한 뒤 서버를 재시작한다.

```dotenv
MAS_AI_URL=https://YOUR-PROVIDER/v1/chat/completions
MAS_AI_MODEL=YOUR-MODEL
MAS_AI_API_KEY=YOUR-KEY
```

로컬 모델 서버도 같은 응답 규격으로 연결할 수 있다. AI 평가 버튼을 누를 때 **파일명과 측정 요약만** 제공자에 보내며 오디오 원본은 보내지 않는다. 제공자를 설정하지 않으면 AI 버튼은 비활성이고 측정 리포트만 동작한다. 연결 정보 미제공 상태에서 실제 외부 LLM 응답은 검증하지 않았다.

진행 상황과 대기 순서는 서버에서 관리한다. 같은 FastAPI 서버에 접속한 브라우저·탭·페이지는 공통 진행 패널을 표시하고 2초마다 갱신한다. 새로고침하거나 창을 닫아도 분석은 계속되며, 다시 접속하면 해당 음원의 요청 상태와 완료 결과를 복원한다.

리포트 분석은 서버 전체에서 한 번에 하나씩 실행한다. 실행/대기 요청이 있으면 일반 즉시 요청은 HTTP 409로 거부하며, **AI 대기열에 추가** 또는 **측정 대기열에 추가**로 명시적으로 접수한 요청을 FIFO 순서로 처리한다. 같은 프로젝트·측정 근거·구간·모델의 동일한 진행/대기 요청은 기존 작업 ID를 반환해 중복 호출하지 않는다. 실행/대기 합계는 최대 20개다. 한 요청이 실패해도 다음 요청은 계속 처리한다.

작업 메타데이터와 결과는 MongoDB의 `report_jobs` 컬렉션(로컬 미리보기는 `storage/jobs/`)에 저장한다. 서버 재시작 시 실행 중이던 요청은 중단 실패로 표시하고, 아직 실행 전인 대기 요청은 복원한다. 제공자 URL/모델 설정이 바뀐 대기 AI 요청은 자동 전송하지 않고 실패 처리한다. API 키는 작업에 저장하지 않는다. 제공자가 진행률을 보내지 않으므로 응답 대기는 단계 상태로 표시한다.

AI는 관찰·근거·연습 제안·한계를 설명한다. 가창 순위, 자의적인 100점 점수, 확정적 창법 판정은 만들지 않는다. 측정 리포트와 AI 해석은 UI에서 구분된다.

## 분석과 제한

- 선택 음원의 분리된 보컬만 측정한다. 분리 잔향·반주 누설·노이즈·무성음은 추정 오차를 늘린다. 악기별 이름은 모델이 실제 제공하는 스템만 표시한다.
- pYIN은 CPU 확률 기반 DSP 분석이며 GPU 신경망 모델이 아니다. [librosa DSP 문서](https://librosa.org/doc/0.11.0/core.html)
- 음 중심 편차는 평균율 음까지의 거리로, 악보 대비 정확도나 가창 실력이 아니다.
- 비브라토와 BPM은 **후보**다. 음색 지표만으로 기식성·두성·믹스·벨팅을 확정하지 않는다.
- 화면 그래프는 최대 약 1,400프레임으로 줄여 표시한다. 선택 구간 리포트도 이 표시용 측정 시계열의 표본을 사용한다.
- 비교는 시간 오프셋 기반이며 자동 음악적 정렬이나 샘플 단위 동기화가 아니다.
- 재생 속도는 음높이를 유지한다. 변형본은 librosa 위상 보코더를 사용해 아티팩트가 생길 수 있다.
- 업로드 원본은 그대로 보관한다. 믹서 재생/분리는 스테레오 44.1kHz WAV, 보컬 수치 분석은 별도 모노 22.05kHz PCM을 사용한다. 파일당 기본 150MB/300초 제한.
- 단일 서버 프로세스/워커로 실행한다. 리포트 대기열은 영속 저장하며 분리·보컬 수치 분석의 실행 큐는 메모리 기반이다. 여러 Uvicorn 워커나 여러 서버 인스턴스가 하나의 큐를 공동 실행하는 구성은 지원하지 않는다.
- 공개 배포용 인증/권한 기능은 없다. 원격 공개 서비스로 사용하기 전 별도 구현이 필요하다.

이전 형식의 프로젝트는 기존 파일과 보컬 측정 결과를 보존해 새 구조로 읽는다. 기존 보컬을 새로 분리한 원본으로 표시하지 않으며, 원본 음악을 새로 추가하거나 실제 재분리를 실행할 수 있다.

## 검증

```powershell
uv sync --locked --group dev
uv run pytest -q
```

합성 정현파로 F0·RMS·침묵·hop 경계, 독립 키/속도 변형, FLAC/스테레오 리샘플링을 검증한다. API 테스트는 원본 업로드→4스템 구조→선택 보컬 분석→캐시→하위 변형→비교 리포트→삭제, 원본 3개 제한, 잘못된 파일, 오디오 Range 재생, 외부 Origin 거부를 포함한다. API 테스트의 분리기는 테스트 전용 대체 구현을 사용한다. 별도로 실제 htdemucs 가중치로 합성 스테레오 신호 4스템 추론과 브라우저 2개 음원 분리를 검증했다. 실제 가수 녹음의 분리 품질 평가는 남아 있다. Jinja 음원 탭, 파일명 이스케이프, Markdown 렌더링과 HTML 차단, 이전 프로젝트 호환도 확인한다. 공유 리포트 큐는 독립 클라이언트의 동시 중복 요청, 직접 요청 거부, FIFO, 실패 후 진행, 재시작 복구와 Mongo 저장 계약을 검증한다. AI 호출은 모의 제공자를 사용한다. Mongo 저장 계약은 mongomock으로 검증하며 실제 SSH 연결은 접속 정보가 필요하다.

구조·지표 해석·다음 단계는 [docs/architecture.md](docs/architecture.md), 디자인 출처는 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)를 참고한다.
