# copilot-patch-libs

orchestrator 컨테이너의 `/opt/copilot-patch-libs` 로 마운트되고 `PYTHONPATH` 에 들어간다.
이미지에 없는 파이썬 패키지를 여기에 설치해 두면 컨테이너를 다시 만들어도 유지된다.

스크린샷 축소(`sitecustomize_image_shrink.py`)에는 Pillow 가 필요하다.
로그에 다음이 보이면 이미지에 Pillow 가 없는 것이다:

    [copilot-image-patch] image shrink failed (Pillow unavailable: No module named 'PIL') -> drop

이때 한 번만 실행하면 된다:

    docker exec copilot-orchestrator pip install --target /opt/copilot-patch-libs pillow
    docker restart copilot-orchestrator

컨테이너에 `pip` 가 없으면 `python -m pip` 또는 `/app/venv/bin/pip` 를 써 볼 것.
그래도 안 되면 `.env` 의 `COPILOT_VISION_MODE=off` 로 두고 텍스트 메타데이터만으로 운영한다.
