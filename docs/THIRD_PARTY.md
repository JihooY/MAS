# 디자인과 의존성 출처

- `frontend/public/reference/theme.css`: 사용자가 지정한 `UnivDash/app/static/theme.css`를 복사해 네 테마의 배색을 유지했다.
- `frontend/public/reference/fonts.css`, `fonts/*.woff2`: 같은 저장소의 로컬 Inter / JetBrains Mono 폰트를 사용했다. SIL Open Font License 원문은 `frontend/public/reference/licenses/`에 포함했다.
- 사이드바, Overview 카드, 설정 모달의 좌측 내비게이션, 모바일 하단 메뉴, 네 테마 선택기는 UnivDash의 레이아웃과 스타일을 React로 옮겨 구성했다. UnivDash의 서버 관리·인증·터미널 코드는 복사하지 않았다.
- React, Vite, lucide-react, Recharts, NumPy, SciPy, librosa, SoundFile, FastAPI, PyMongo와 imageio-ffmpeg는 각 패키지의 라이선스를 따른다. 설치 환경의 패키지 메타데이터와 포함된 라이선스를 확인할 수 있다.
- 외부 신경망 모델 가중치는 다운로드하지 않는다. 연결하는 LLM 제공자의 모델 라이선스/서비스 조건은 사용자가 선택한 제공자에 따른다.

기술 참고: [librosa DSP](https://librosa.org/doc/0.11.0/core.html), [독립 키/속도 변형](https://librosa.org/doc/0.11.0/effects.html), [PyMongo SSH 터널 연결](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-targets/), [uv 동기화](https://docs.astral.sh/uv/concepts/projects/sync/).
