# 디자인과 의존성 출처

- `backend/app/static/reference/theme.css`: 사용자가 지정한 `UnivDash/app/static/theme.css`를 복사해 네 테마의 배색을 유지했다.
- `backend/app/static/reference/fonts.css`, `fonts/*.woff2`: 같은 저장소의 로컬 Inter / JetBrains Mono 폰트를 사용했다. SIL Open Font License 원문은 `backend/app/static/reference/licenses/`에 포함했다.
- 사이드바, Overview 카드, 설정 모달의 좌측 내비게이션, 모바일 하단 메뉴, 네 테마 선택기는 UnivDash의 레이아웃과 스타일을 Jinja 템플릿과 일반 JavaScript로 옮겨 구성했다. UnivDash의 서버 관리·인증·터미널 코드는 복사하지 않았다.
- Lucide SVG 아이콘, Jinja2, NumPy, SciPy, librosa, SoundFile, FastAPI, PyMongo와 imageio-ffmpeg는 각 패키지의 라이선스를 따른다. 설치 환경의 패키지 메타데이터와 포함된 라이선스를 확인할 수 있다.
- Demucs 4.0.1과 PyTorch/torchaudio를 실제 스템 분리에 사용한다. Demucs는 MIT 라이선스이며 원문을 `backend/app/static/reference/licenses/Demucs-LICENSE.txt`에 포함했다. 공식 pretrained 모델 가중치를 첫 사용 시 `storage/models/`에 다운로드하고 재사용한다. 모델 설명과 라이선스 출처: [공식 Demucs 저장소](https://github.com/facebookresearch/demucs). 연결하는 LLM 제공자의 모델 라이선스/서비스 조건은 사용자가 선택한 제공자에 따른다.

- Lucide는 정적 SVG 스프라이트(`backend/app/static/icons.svg`)로만 사용하며 React 패키지는 필요하지 않다. 라이선스 원문은 `backend/app/static/reference/licenses/Lucide-LICENSE.txt`에 포함했다.

기술 참고: [librosa DSP](https://librosa.org/doc/0.11.0/core.html), [독립 키/속도 변형](https://librosa.org/doc/0.11.0/effects.html), [PyMongo SSH 터널 연결](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-targets/), [uv 동기화](https://docs.astral.sh/uv/concepts/projects/sync/).

- Markdown은 markdown-it-py로 렌더링하고 Bleach로 허용 태그·링크만 남긴다. 각 패키지에 포함된 MIT / Apache-2.0 라이선스를 따른다.
