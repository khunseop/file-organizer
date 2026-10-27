# 작업 파일 자동 정리 도구

소스 폴더의 파일을 파일명 규칙으로 묶어 `날짜_작업_대상` 폴더로 정리합니다. 규칙 기반이라 AI 없이 폐쇄망에서도 동작합니다.

## 준비

```
pip install pyyaml
cp rules.example.yaml rules.yaml   # 경로와 키워드 수정
python3 organizer.py check         # 규칙 검증
```

## 사용

```
python3 organizer.py               # Dry Run (기본): 묶음과 이동 계획만 출력
python3 organizer.py run --apply   # 확인 후 실제 이동, 이어서 미분류 대화형 처리
python3 organizer.py edit          # 브라우저에서 규칙 편집 (http://127.0.0.1:8765)
python3 organizer.py undo          # 마지막 실행 되돌리기 (--run-id 로 지정 가능)
```

옵션: `-c 규칙파일`, `run -y`(확인 생략), `run --no-interactive`(미분류 처리 생략)

## 규칙 편집기

`edit`는 내 PC(127.0.0.1)에서만 열리는 로컬 서버를 띄우고 브라우저를 엽니다. 작업/대상/무시 규칙을 추가·수정·삭제하고 키워드(Enter로 추가)와 우선순위(↑↓)를 바꿉니다. 오른쪽 미리보기는 저장 전 규칙으로 소스 폴더 파일의 분류 결과를 보여주고, 파일명을 직접 넣어 시험할 수도 있습니다. 저장은 Ctrl/Cmd+S이며 종료는 터미널에서 Ctrl+C입니다. `organizer.py`와 `editor.html`을 같은 폴더에 두세요.

## 동작 규칙

- 작업과 대상이 모두 매치돼야 분류됩니다. 키워드는 대소문자 무시 부분 일치이고, 위에서 아래로 첫 매치가 우선입니다.
- 대상이 없는 파일은 미분류입니다. 미분류에서 `r`(규칙 추가), `s`(이번만 건너뛰기), `i`(분류 불필요 → ignore 추가)를 고릅니다.
- 날짜는 `date_source`에 따라 `filename → mtime → today` 또는 `mtime → filename → today` 순입니다.
- 덮어쓰지 않고, 이름이 겹치면 `_1`, `_2`를 붙입니다.
- 소스 폴더 바로 아래 파일만 대상이고 하위 폴더는 보지 않습니다. `output_dir`은 항상 제외됩니다.
- 로그는 `organizer_log.jsonl`(규칙 파일 옆, `log_file`로 변경)에 이동·되돌리기 내역을 쌓습니다. 자동 삭제하지 않습니다.
- 대화형으로 규칙을 추가하거나 ignore를 바꾸면 `rules.yaml`을 다시 쓰며, 이때 YAML 주석은 사라집니다.

## 테스트

```
python3 -m unittest -v
```
