# 배포 관리자: 새 버전 발행

1. 변경을 검증하고 `pyproject.toml`, `nanocofold/__init__.py`의 버전을 함께 올립니다.
2. `CHANGELOG.md`에 변경 사항, 호환성, 검증 범위를 기록합니다.
3. `python -m pytest`와 설치/예제 검증을 수행합니다. GPU 예제를 실행하지 않았다면 명시합니다.
4. 소스·wheel·Git 이력에 개인 경로, 토큰, 연구 입력/결과가 들어가지 않았는지 확인합니다.
5. `main`에 반영하고 CI가 통과한 commit에 `vX.Y.Z` 태그를 붙입니다.
6. 같은 commit에서 소스 ZIP과 wheel을 만들고 SHA256SUMS를 생성합니다.
7. GitHub Release에 변경 기록과 검증 범위, 세 파일을 첨부합니다.

예시(버전 번호는 실제 발행 버전으로 바꿉니다):

```bash
python -m build --wheel
git tag -a vX.Y.Z -m 'NanoCoFold vX.Y.Z'
git archive --format=zip --prefix=NanoCoFold_public-X.Y.Z/ \
  -o dist/NanoCoFold_public-X.Y.Z.zip vX.Y.Z
(cd dist && sha256sum NanoCoFold_public-X.Y.Z.zip nanocofold-X.Y.Z-py3-none-any.whl > SHA256SUMS)
git push origin main vX.Y.Z
```

GitHub의 Releases → Draft a new release에서 해당 태그를 고릅니다.
발행한 태그·파일을 덮어쓰지 않습니다. 결함이 발견되면 새 PATCH 버전으로 고칩니다.
모델 가중치, 가상환경, 분석 원본, 생성 보고서는 Release에 넣지 않습니다.
