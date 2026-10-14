# 배포 관리자: 후보와 정식 버전 발행

배포 후보는 `0.4.8rc2`처럼 `rc`와 후보 번호를 사용하고 정식 버전은 `0.4.8`처럼 구분합니다. `pyproject.toml`과 `nanocofold/__init__.py`를 같은 버전으로 맞추고 CHANGELOG에 변경·호환성·실제 검증 범위를 기록합니다. 후보를 정식 버전의 완료 검증으로 표시하지 않습니다.

## 공동연구자 검토용 묶음

1. 필요한 CPU·실제 브라우저·설치된 wheel 검사를 수행하고 입력·결과·한계를 확인합니다. 합성 workflow, 보존된 과거 GPU 결과와 새 GPU 실행은 별도로 기록합니다.
2. 전체 source 파일 목록과 SHA를 고정합니다. 실제 Git HEAD도 기록하고, 미커밋 snapshot이면 해당 HEAD의 원본과 같은 코드라고 주장하지 않고 변경 상태와 정확한 source digest를 함께 기록합니다.
3. 최종 wheel의 직접 소스와 pyproject `force-include` 자원이 해당 source와 바이트가 같은지 확인합니다. 계산 당시 코드와 표시 코드를 구분합니다.
4. 새로운 이름의 전달 ZIP을 만들고 manifest·CRC·fresh unzip·privacy·상대 링크·입력 대응을 확인합니다. 이전 후보 ZIP·원본 계산·실패 영수증은 보존합니다.
5. 실제 완료한 상태에서만 `delivery_ready`를 기록합니다. 마지막 source/HEAD/wheel/ZIP 해시와 확인 범위를 같이 전달합니다.

공동연구자 검토용 ZIP을 만드는 작업은 Git 태그나 GitHub Release 발행을 자동으로 포함하지 않습니다. 태그나 원격 Release는 별도 발행 요청이 있을 때 아래 절차로 진행합니다.

## 요청받은 GitHub 발행

1. 위 검증을 완료하고 최종 변경을 commit합니다. GitHub Actions의 실제 성공을 확인한 commit을 사용합니다. CI 설정이 존재한다는 사실만으로 성공을 기록하지 않습니다.
2. 후보는 `v0.4.8rc2`, 정식은 `v0.4.8`처럼 정확한 태그를 사용합니다. 같은 commit에서 source ZIP과 wheel을 만들고 SHA256SUMS를 생성합니다.
3. 요청받은 태그를 push하고 GitHub의 Releases → Draft a new release에서 해당 태그를 선택합니다. 후보는 **pre-release**로 표시합니다.
4. 변경 기록·검증 범위·source/wheel/checksum을 첨부합니다. 협업 예측 보고서 묶음은 별도 전달 자료로 구분합니다.

버전과 태그는 실제 승인한 발행 대상으로 바꿉니다. 아래는 발행 요청 후 사용하는 관리자 예시입니다.

```bash
release_version=0.4.8rc2
release_tag="v${release_version}"
python -m build --wheel
git tag -a "$release_tag" -m "NanoCoFold ${release_version}"
git archive --format=zip --prefix="NanoCoFold_public-${release_version}/" \
  -o "dist/NanoCoFold_public-${release_version}.zip" "$release_tag"
sha256sum "dist/NanoCoFold_public-${release_version}.zip" \
  "dist/nanocofold-${release_version}-py3-none-any.whl" > dist/SHA256SUMS
git push origin main "$release_tag"
```

발행한 태그·파일은 덮어쓰지 않습니다. 후보 결함은 다음 rc 번호, 정식 발행 후 결함은 새 PATCH 버전으로 구분합니다. 모델 가중치·가상환경·개인 설정·원본 연구 결과는 source/wheel Release에 넣지 않습니다.
