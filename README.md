# Music Analysis Studio · MAS

UnivDash 디자인을 바탕으로 만든 한국어 **프로젝트 기반 음악 스튜디오**. FastAPI + Jinja2 + 일반 JavaScript + NumPy/librosa, uv 기반 Python 환경, MongoDB/OpenSSH 연결을 사용한다. 원본 음악을 보컬·악기로 분리하고, 분리된 보컬을 분석한다. 악기의 연주 분석은 포함하지 않는다.

## 구현 기능

- 프로젝트 생성·이름 변경·저장·다시 열기·삭제
- Gmail 주소·MAS 전용 비밀번호 회원가입/로그인, 로그아웃 및 계정별 프로젝트·음원·리포트 접근 제한
- 설정 → 언어에서 한국어/영어 전환, 로그인 화면 언어 선택, 브라우저별 언어 유지 및 새 리포트의 출력 언어 적용
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

`uv` 캐시 권한 오류가 있는 제한 환경에서는 `$env:UV_CACHE_DIR = "$PWD/.cache/uv"`를 설정한다. 일반 실행에서는 필요 없다. 서버는 단일 프로세스로 실행해야 한다.

## 계정과 관리자 설정

접속하면 `/login`으로 이동한다. `/register`에서 Gmail 주소와 **MAS에서 사용할 비밀번호(12~256자)**를 정한다. Google 로그인/OAuth가 아니며 Google 계정 비밀번호를 입력하지 않는다. 현재 메일 소유 확인·인증 메일·비밀번호 재설정은 구현하지 않았다. Gmail의 점(`.`)과 `+태그`, 대소문자는 동일 주소로 처리한다.

임시 관리자 계정은 `.env`에 아래 두 값을 함께 설정한 뒤 서버를 재시작한다. 실제 사용할 Gmail 주소와 충분히 긴 비밀번호로 바꾼다.

```dotenv
MAS_ADMIN_EMAIL=yourname@gmail.com
MAS_ADMIN_PASSWORD=replace-with-your-own-long-password
MAS_SESSION_HTTPS_ONLY=false
MAS_SESSION_MAX_HOURS=12
```

관리자는 시작 시 생성/갱신되며, **이전 공용 프로젝트 중 소유자 없는 데이터는 이 관리자에게 귀속**된다. 관리자를 설정하지 않으면 기존 공용 데이터는 보존하되 모든 일반 계정에서 숨긴다. 관리자도 다른 사용자의 개인 프로젝트에 접근하지 않는다. 관리자 비밀번호 변경은 이전 세션을 무효화하며, 관리자 주소 변경/설정 제거는 이전 환경변수 관리자 계정의 로그인을 비활성화한다. 설정은 재시작 시 반영된다.

비밀번호는 PBKDF2-SHA256(600,000회·무작위 salt) 해시로만 저장한다. 무작위 세션 토큰은 HttpOnly/SameSite=Lax 쿠키로 발급하며 서버에는 토큰 해시만 저장한다. 세션은 기본 12시간 뒤 만료되고 로그아웃 시 즉시 폐기된다. 로그인/가입은 IP별 15분에 10회로 제한한다. HTTPS 프록시 뒤에서는 `MAS_SESSION_HTTPS_ONLY=true`로 설정한다. 로컬 HTTP에서는 false를 사용한다.

사용자와 세션은 기존 저장 모드를 따른다(MongoDB `users`/`auth_sessions`, 로컬 `storage/users/`·`storage/auth_sessions/`). 내부 사용자 ID와 프로젝트 `owner_id`로 권한을 검사하므로 나중에 Discord 계정을 연결해도 프로젝트 소유권 키를 바꿀 필요가 없다. Discord 연결 기능 자체는 아직 포함하지 않는다.

## 표시 언어

설정의 **언어 / Language** 탭에서 **한국어 / English**를 선택한다. 로그인·회원가입 화면에서도 바꿀 수 있다. 기본은 한국어이며 선택은 이 브라우저의 `mas-language` 쿠키에 1년간 저장된다. 로그아웃 후에도 유지되며 다른 브라우저의 선택에는 영향을 주지 않는다. 언어 변경 시 현재 주소에서 새로고침하고 재생을 멈춘다. 설정에서 변경했다면 언어 탭을 다시 연다.

메뉴, 도움말, 접근성 레이블, 알림·오류, 작업 진행 상태, 그래프 안내, 기본 스템 이름과 새 측정 리포트가 선택 언어로 표시된다. AI 평가도 요청 시 선택한 언어를 사용하며 대기열·재시작 후에도 그 언어를 유지한다. 기존에 저장된 리포트, 사용자가 정한 프로젝트명과 업로드한 파일명은 번역하거나 덮어쓰지 않는다.

번역 목록은 `backend/app/static/locales/en.json`을 Python/Jinja와 JavaScript가 함께 사용한다. 번역에 사용자 이름을 포함할 때도 HTML 이스케이프를 유지한다.

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

`.env`에서 **실제 음악 청취 모델**과 **최종 해석 모델**을 독립적으로 설정하고 서버를 재시작한다. URL은 기본 주소(`/v1`)와 전체 Chat Completions 주소를 모두 지원한다. API 키는 FastAPI 서버에서만 사용하며 브라우저에 전달하지 않는다.

```dotenv
AUDIO_AI_URL=https://YOUR-MUSIC-SERVER/v1/chat/completions
AUDIO_AI_MODEL=nvidia/music-flamingo-hf
AUDIO_AI_API_KEY=YOUR-MUSIC-SERVER-KEY

LLM_AI_URL=https://YOUR-LLM-SERVER/v1/chat/completions
LLM_AI_MODEL=openai/gpt-oss-120b
LLM_AI_API_KEY=YOUR-LLM-SERVER-KEY
```

`AUDIO_*`는 Music Flamingo, `LLM_*`는 gpt-oss-120b다. 기존 `MAS_AI_URL/MODEL/API_KEY` 설정은 이 두 그룹으로 교체한다. 같은 OpenAI Chat Completions JSON 형식을 기존 HTTPX 어댑터로 요청하므로 OpenAI SDK의 추가 설치는 필요 없다. MAS 자체 기본 포트는 8000이므로 같은 PC에서 LLM 서버도 8000을 사용하면 MAS_PORT를 변경해야 한다.

사용 순서: 음악 A/B/C 업로드 → 기존 Demucs 분리 → 기본 보컬 분석 켜기 → 구간/비교 대상 선택 → **AI 분석**. Music Flamingo는 동일 구간의 **Full Mix / Vocal / Instrumental**을 별도의 요청으로 듣는다. Instrumental은 보컬을 제외한 실제 분리 스템의 합이다. gpt-oss는 세 유형을 각각 해석·비교한 뒤 네 번째 요청으로 곡 전체 맥락, 보컬·반주 상호작용, 잘한 점·아쉬운 점·개선 사항과 10점 평가를 통합한다.

곡 전체 흐름 요약과 선택 구간의 상세 결과는 분리한다. 선택 구간이 곡 전체가 아니면 Full Mix 전체를 추가로 청취한다. 인트로·벌스·후렴 등 이름 있는 구간을 직접 저장하고 다시 선택할 수 있다. 자동 구조 분류를 확정한 것처럼 표시하지 않는다. API 요청 한도 안에서 저장된 구간 경계를 우선 사용하고, 긴 구간은 RMS가 낮은 경계 후보로 나눠 모든 프레임을 포함한다. 세 입력은 Full Mix 기준의 동일한 구간 계획을 공유한다. 변형 보컬과 이전 보컬 전용 프로젝트는 시간축/원본/반주가 없어 통합 평가를 보류하며 기존 DSP·재생·변형 기능은 유지한다.

보고서는 **통합 결과 / 전체 음악 / 보컬 / 반주** 탭과 음원별 점수 카드로 제공한다. 평가 비중은 조화 25%, 목소리와 편곡의 어울림 15%, 창법·제어 20%, 기본기·리듬·프레이징 20%, 음색 10%, 감정선·다이내믹 표현 10%다. 각 항목은 근거·신뢰도·불확실성을 갖는 주관적 AI 추정이다. 백엔드가 여섯 항목이 모두 평가된 경우에만 고정 가중 평균을 계산하며 근거 부족 항목을 0점으로 만들지 않는다. 음 중심 편차를 가창 정확도 점수로 환산하지 않는다. [구조와 점수 기준](docs/music-context.md)을 참고한다.

각 AI 요청은 두 서버의 `/v1/models`에서 설정한 ID가 실제 제공되는지 확인한다. ID가 다르면 실패하며 다른 모델로 자동 변경하지 않는다. 실제 WAV 요청의 성공 전에는 오디오 입력 지원을 검증된 것으로 표시하지 않는다. 형식은 `input_audio`의 Base64 WAV(모노 24kHz PCM16)다. 요청 한도는 기본 30초이며 음악 구간/낮은 RMS 경계 후보를 우선 사용해 나눈다. 선택 구간의 마지막 부분을 버리지 않는다. 파일명·모델 관찰은 지시가 아닌 데이터로 취급한다. API 오류/인증/타임아웃/빈 응답/토큰 제한으로 잘린 응답은 실패 상태와 안전하게 정리한 서버 로그로 남긴다.

실제 연결 진단은 `.env` 설정 후 다음 명령으로 실행한다. 실제 음악 WAV의 첫 최대 5초를 전송한다. 모델 목록만 확인하거나 파일을 생략하면 음악 입력 검증 성공으로 처리하지 않는다. 진단은 Full Mix 입력과 LLM 텍스트 연결만 확인한다. three_way_pipeline_verified=false로 기록하며, 3종 분석·구조화된 응답·DSP 통합·채점은 스튜디오의 AI 분석에서 별도로 확인해야 한다.

```powershell
uv run python -m backend.check_ai --audio "C:/music/sample.wav" --output storage/ai-connection-check.json
```

종료 코드 0은 두 모델 ID 확인과 실제 WAV 청취·최종 해석 요청이 모두 성공했음을 뜻한다. 서버가 스키마를 받아 텍스트를 반환하는지 검증하며, 분석의 음악적 정확도는 별도 전문가 검토가 필요하다. 모델 파일 이름이 같아도 서버의 vLLM 버전·모델 구현·chat template에 따라 지원 여부가 달라질 수 있다. [vLLM 오디오 입력](https://docs.vllm.ai/en/latest/features/multimodal_inputs/#audio-inputs), [NVIDIA Music Flamingo 모델 카드](https://huggingface.co/nvidia/music-flamingo-hf), [OpenAI 오디오 요청 형식](https://developers.openai.com/api/docs/guides/audio-chat-completions)을 참고한다.

진행 상황과 대기 순서는 서버에서 관리한다. 같은 계정으로 로그인한 브라우저·탭·페이지는 해당 계정의 공통 진행 패널을 표시하고 2초마다 갱신한다. 새로고침하거나 창을 닫아도 분석은 계속되며, 다시 접속하면 해당 음원의 요청 상태와 완료 결과를 복원한다. 서버 전체의 처리 중 여부와 대기 순번은 공유하지만 다른 계정의 프로젝트명·작업 내용·결과는 노출하지 않는다.

리포트 분석은 서버 전체에서 한 번에 하나씩 실행한다. 실행/대기 요청이 있으면 일반 즉시 요청은 HTTP 409로 거부하며, **AI 대기열에 추가** 또는 **측정 대기열에 추가**로 명시적으로 접수한 요청을 FIFO 순서로 처리한다. 같은 프로젝트·측정 근거·구간·모델의 동일한 진행/대기 요청은 기존 작업 ID를 반환해 중복 호출하지 않는다. 실행/대기 합계는 최대 20개다. 한 요청이 실패해도 다음 요청은 계속 처리한다.

작업 메타데이터와 결과는 MongoDB의 `report_jobs` 컬렉션(로컬 미리보기는 `storage/jobs/`)에 저장한다. 서버 재시작 시 실행 중이던 요청은 중단 실패로 표시하고, 아직 실행 전인 대기 요청은 복원한다. 두 제공자의 URL/모델/키 또는 요청 설정이 바뀐 대기 AI 요청은 자동 전송하지 않고 실패 처리한다. API 키와 Base64 음원은 작업에 저장하지 않는다. 청취 결과·정확한 구간·WAV SHA-256·모델·검증 결과·DSP 근거·최종 보고서는 저장한다. 최종 LLM이 실패해도 완료한 청취 결과는 해당 작업에 남는다. 전문가 검토·학습을 위한 원자료이며 전문가 피드백 입력 시스템과 파인튜닝은 이번 단계에 포함하지 않는다. 제공자가 진행률을 보내지 않으므로 응답 대기는 단계 상태로 표시한다.

AI는 음악적 맥락·관찰·근거·연습 제안·한계를 설명하고, 녹음별 10점 추정 평가를 제공한다. 사람의 보편적 가창 실력을 확정하거나 창법·감정·음악적 의도를 사실로 단정하지 않는다. 측정 리포트와 AI 해석은 UI에서 구분된다.

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
- Gmail 주소·비밀번호 인증과 계정별 접근 권한을 제공한다. 현재 이메일 소유 확인·비밀번호 재설정·Discord 연결은 제공하지 않는다.

이전 형식의 프로젝트는 기존 파일과 보컬 측정 결과를 보존해 새 구조로 읽는다. 기존 보컬을 새로 분리한 원본으로 표시하지 않으며, 원본 음악을 새로 추가하거나 실제 재분리를 실행할 수 있다.

## 검증

```powershell
uv sync --locked --group dev
uv run pytest -q
```

합성 정현파로 F0·RMS·침묵·hop 경계, 독립 키/속도 변형, FLAC/스테레오 리샘플링을 검증한다. API 테스트는 원본 업로드→4스템 구조→선택 보컬 분석→캐시→하위 변형→비교 리포트→삭제, 원본 3개 제한, 잘못된 파일, 오디오 Range 재생, 외부 Origin 거부를 포함한다. API 테스트의 분리기는 테스트 전용 대체 구현을 사용한다. 별도로 실제 htdemucs 가중치로 합성 스테레오 신호 4스템 추론과 브라우저 2개 음원 분리를 검증했다. 실제 가수 녹음의 분리 품질 평가는 남아 있다. Jinja 음원 탭, 파일명 이스케이프, Markdown 렌더링과 HTML 차단, 이전 프로젝트 호환도 확인한다. 공유 리포트 큐는 독립 클라이언트의 동시 중복 요청, 직접 요청 거부, FIFO, 실패 후 진행, 재시작 복구와 Mongo 저장 계약을 검증한다. AI 호출은 모의 제공자를 사용한다. Mongo 저장 계약은 mongomock으로 검증하며 실제 SSH 연결은 접속 정보가 필요하다.

구조·지표 해석·다음 단계는 [docs/architecture.md](docs/architecture.md), 디자인 출처는 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)를 참고한다.
