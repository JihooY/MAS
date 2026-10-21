# 음악 AI 통합 검증 기록

검증일: 2026-10-21 (Asia/Singapore).

## 구현한 흐름

음악 업로드 → 기존 Demucs 분리 → 보컬 DSP 분석 → 구간 선택 및 AI 분석 요청 → 두 서버의 모델 ID 확인 → Music Flamingo의 원본/보컬 WAV 청취 → DSP 근거와 청취 관찰 통합 → gpt-oss-120b 보고서 생성 → 작업/결과 저장.

- `AUDIO_AI_URL`, `AUDIO_AI_MODEL`, `AUDIO_AI_API_KEY`: 음악 청취 서버.
- `LLM_AI_URL`, `LLM_AI_MODEL`, `LLM_AI_API_KEY`: 최종 보고서 서버.
- 키는 백엔드에서만 사용한다. 작업 데이터·로그·브라우저로 키를 전달하지 않는다.
- AI 분석은 202 접수 후 영속 FIFO 워커에서 처리한다. 성공한 청취 결과는 매 구간마다 저장하며 최종 LLM 실패 시에도 남긴다.
- 원본 없는 이전 프로젝트와 변형 보컬은 해당 보컬만 청취한다.

## 실제 서버 검증 결과

사용자가 이번 단계에서 연결 설정은 나중에 입력하기로 선택했다. 저장소의 `.env`에 두 모델 ID와 URL/키 입력 항목을 준비했으며, 실제 주소와 키는 비워 두었다. 아래 결과는 연결 거절이나 모델 호환성 실패를 뜻하지 않는다. 설정이 없어 네트워크 요청을 시작하지 못한 상태다.

| 항목 | 이번 결과 |
| --- | --- |
| AUDIO 설정 확인 | `AUDIO_AI_URL`, `AUDIO_AI_API_KEY` 미입력 |
| LLM 설정 확인 | `LLM_AI_URL`, `LLM_AI_API_KEY` 미입력 |
| 두 서버 `/v1/models` 실제 응답 | 미검증 / 요청 미실행 |
| 실제 Music Flamingo WAV 입력 지원 | 미검증 / 전송 미실행 |
| 실제 음악 분석 및 gpt-oss 응답 | 미검증 / 요청 미실행 |
| 진단 프로그램 종료 코드 | 1: 설정 누락 감지 |

실행 명령: `python -m backend.check_ai --output storage/ai-connection-check.json`. 결과 JSON은 로컬 런타임 저장소에 기록했다. 기본 모델명은 서버의 별칭과 다를 수 있으므로 실제 `/models` 목록과 정확히 일치하도록 설정해야 한다. 호환성을 확인하지 않은 모델·음성 출력 기능은 성공으로 표시하지 않는다.

설정 후 실제 음악 WAV로 다음을 실행한다. 기본 API 주소(`/v1`)와 전체 `/v1/chat/completions` URL을 모두 지원한다.

```powershell
uv run python -m backend.check_ai --audio "C:/music/sample.wav" --output storage/ai-connection-check.json
```

이 명령은 첫 최대 5초의 WAV를 보내 두 모델 ID, `input_audio` 요청, 최종 텍스트 응답을 검증한다. 테스트 파일 없이 모델 목록만 성공해도 오디오 호환성 검증 성공으로 간주하지 않는다. CLI 진단은 DSP 근거를 만들지 않으므로 전체 통합은 스튜디오에서 보컬 분석 후 AI 분석으로 확인한다. 분석의 음악적 정확도는 전문가 검토가 필요하다.

## 자동 검증

전체 기존/신규 테스트 58개가 통과했다. 이후 추가한 진단 WAV 성공 경로와 번역 정리를 포함한 음악 AI/번역 테스트 32개도 통과했다(신규 1개 포함, 전체 고유 테스트 수 59개). 테스트의 외부 AI는 HTTPX MockTransport를 사용하며 Demucs의 대형 가중치 추론은 대체한다. 실제 배포 서버나 실제 가수 음악의 품질 검증을 의미하지 않는다.

검증 범위는 다음과 같다.

- 독립 `.env` 로딩, 기본/전체 URL 정규화, URL 내 자격증명 차단.
- 실제 오디오 디코딩, 선택 구간의 연속 프레임 분할, PCM16 WAV·24kHz·모노 형식과 음량 보존.
- `/models` ID 확인, 역할별 인증 헤더, Music Flamingo에만 Base64 오디오 전송.
- 실제 업로드/파일/DSP/큐/저장 코드와 모의 API를 연결한 성공·Flamingo 실패·gpt-oss 실패 흐름.
- 모델 불일치, 서버 스키마 거절, 타임아웃, 잘못된 JSON, 빈 응답, 토큰 제한 중단의 명시적 오류.
- 원본/분리 보컬 구분, 변형 보컬의 시간축 오인 방지, 디렉터리 이탈 차단.
- 실패 시 부분 청취 결과 보존, 저장/응답/로그의 키 비노출.
- 기존 분리·재생·변형·비교·삭제·인증·한영 UI·리포트 큐 회귀 검사.

이 Windows 샌드박스는 asyncio 내부 loopback 소켓 생성을 차단해 FastAPI TestClient가 시작 시 대기했다. 테스트는 샌드박스 밖의 로컬 실행으로 확인했다. 테스트용 임시 디렉터리는 저장소의 `.cache` 안에 두었다. Starlette의 기존 HTTPX TestClient deprecation 경고 1건이 있으며 실패는 아니다.

## 다음 단계

실제 서버 설정 후 라이브 호환성/음악 품질 검증, 관리자 전문가 검토 시스템과 피드백 입력, 학습 데이터 승인·내보내기 절차, 실제 파인튜닝은 남아 있다. 현재는 원자료와 보고서를 저장하고 JSON으로 내보내는 단계이며 학습을 자동 실행하지 않는다.

## 확인한 공식 문서

- [vLLM 오디오 입력](https://docs.vllm.ai/en/latest/features/multimodal_inputs/#audio-inputs): OpenAI 호환 오디오 메시지 형식.
- [NVIDIA Music Flamingo 모델 카드](https://huggingface.co/nvidia/music-flamingo-hf): 모델 역할과 오디오 처리 특성.
- [OpenAI 오디오 Chat Completions](https://developers.openai.com/api/docs/guides/audio-chat-completions): Base64 `input_audio` 구조.
