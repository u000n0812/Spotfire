# 데이터 로더 컨테이너 전용 SSL 검증 우회 패치.
#
# 사내망이 HTTPS 를 검사(self-signed 인증서로 교체)하는 환경에서는
# hi_res 전략이 첫 실행 시 HuggingFace 에서 레이아웃 분석 모델을,
# unstructured 가 NLTK 데이터를 다운로드할 때
# CERTIFICATE_VERIFY_FAILED 오류가 발생함.
# 이 파일을 site-packages/sitecustomize.py 로 넣으면 파이썬 시작 시
# 자동 로드되어 인증서 검증을 비활성화함.
#
# 주의: 신뢰된 사내망 + 로컬 개발 용도 한정. 외부 운영 환경에서는
# 사내 프록시의 루트 CA 를 컨테이너에 설치하는 방식을 권장.

import ssl

# urllib / nltk 등 표준 라이브러리 기반 다운로드
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

# requests / huggingface_hub 기반 다운로드
try:
    import urllib3

    urllib3.disable_warnings()
except Exception:
    pass

try:
    import requests

    _orig_session_init = requests.Session.__init__

    def _session_init(self, *args, **kwargs):
        _orig_session_init(self, *args, **kwargs)
        self.verify = False

    requests.Session.__init__ = _session_init
except Exception:
    pass
