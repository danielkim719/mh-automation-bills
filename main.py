import os
import re
import sys
import time
import csv
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import configparser

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

config_path = BASE_DIR / 'config.ini'

config = configparser.ConfigParser()
config.read(config_path, encoding='utf-8')

USER_ID = config["DEFAULT"]["USER_ID"]
USER_PW = config["DEFAULT"]["USER_PW"]

DOWNLOAD_DIR = BASE_DIR / "bills"
DOWNLOAD_DIR.mkdir(exist_ok=True)


def wait_for_new_pdf(directory: Path, old_pdfs: set[str], timeout: float = 30.0) -> str:
    """
    directory 내에서 old_pdfs에 없던 .pdf 파일이 생길 때까지 대기하고, 새로 생긴 파일명을 반환
    """
    end = time.time() + timeout
    while time.time() < end:
        current = {f for f in os.listdir(directory) if f.lower().endswith(".pdf")}
        new = current - old_pdfs
        if new:
            # 보통은 하나만 생기니 바로 반환
            return new.pop()
        time.sleep(0.5)
    raise TimeoutError("새로운 PDF 파일 생성 대기 시간 초과")


def download_and_rename_for_org(driver, wait, org_id: str, org_name: str, year: int, month: int):
    # 1) 다운로드 전 기존 PDF 목록 기억
    old_pdfs = {f for f in os.listdir(DOWNLOAD_DIR) if f.lower().endswith(".pdf")}

    # 2) 첫 번째 행 (No. 컬럼 값이 1인 행)의 파일 다운로드 아이콘 클릭
    pdf_icon = wait.until(EC.element_to_be_clickable((
        By.XPATH,
        "//div[contains(@class,'sc-6a03363a-3') and normalize-space(text())='1']"
        "/ancestor::div[contains(@class,'sc-6a03363a-1')]"
        "//div[contains(@class,'dedrnh')]/img"
    )))
    pdf_icon.click()

    # 3) 새 PDF 파일이 생길 때까지 대기
    try:
        new_pdf = wait_for_new_pdf(DOWNLOAD_DIR, old_pdfs, timeout=60)
    except TimeoutError as e:
        print(f"[{org_id}] PDF 생성 실패:", e)
        return

    # 4) 조직 정보 사용하여 파일명 변경: "{YY}.{MM} ZENICOG 이용 요금 청구서_{name}.pdf"
    src = DOWNLOAD_DIR / new_pdf
    yy = year % 100
    mm = f"{month:02d}"
    safe_name = org_name.replace("/", "_")  # 혹시라도 있을 파일명 오류 제거
    dst_name = f"{yy:02d}.{mm} ZENICOG 이용 요금 청구서_{safe_name}.pdf"
    dst = DOWNLOAD_DIR / dst_name

    os.rename(src, dst)
    print(f"[{org_id}] 리네임 완료: {new_pdf} → {dst_name}")


def download_bills_for_organizations(orgs: list[dict], year: int, month: int):
    # 셀레늄 실행 전, 다운로드 폴더 강제 지정
    options = webdriver.ChromeOptions()
    options.add_experimental_option("prefs", {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
    })
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)

    # 0) 로그인
    driver.get("https://stage.d3l8lrlzxpuhlm.amplifyapp.com/login")
    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[type='text']"))).send_keys(USER_ID)
    driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(USER_PW)
    driver.find_element(By.CSS_SELECTOR, "button").click()
    wait.until(EC.url_contains("/organization/"))

    for org in orgs:
        org_id = org["id"]
        org_name = org["name"]
        # 1) 조직 페이지로 이동
        driver.get(f"https://stage.d3l8lrlzxpuhlm.amplifyapp.com/organization/{org_id}")

        # 2) 요금제 표 로드 대기 및 페이지 맨 아래로 스크롤
        wait.until(EC.presence_of_element_located((By.XPATH,
                                                   "//div[contains(@class,'sc-6a03363a-3') and normalize-space(text())='1']"
                                                   )))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        # 3) 이미지 아이콘 클릭하여 다운로드 및 파일명 변경
        download_and_rename_for_org(driver, wait, org_id, org_name, year, month)

    driver.quit()


def main():
    # 1) exe 실행 파일이 들어잇는 폴더 위치 확인
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent

    # 2) YYYY-MM-bills.csv 파일 찾아서 연도와 월을 지정
    candidates = list(base_dir.glob("*-bills.csv"))
    pattern = re.compile(r"^\d{4}-\d{2}-bills\.csv$")
    csv_files = [p for p in candidates if pattern.match((p.name))]

    if len(csv_files) != 1:
        print(f"❌ `{base_dir}` 폴더에 `YYYY-MM-bills.csv` 파일이 없거나 두 개 이상입니다. 한 개의 파일만 준비해 주세요.")
        input("엔터를 누르면 종료됩니다…")
        return

    csv_path = csv_files[0]

    year, month, _ = csv_path.stem.split("-")
    year, month = int(year), int(month)

    # 3) CSV에서 subscription_type이 "usage_based"인 id, name만 읽어 리스트로
    orgs: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("subscription_type", "").strip() == "usage_based":
                org_id = row.get("id", "").strip()
                org_name = row.get("name", "").strip()
                if org_id and org_name:
                    orgs.append({"id": org_id, "name": org_name})

    if not orgs:
        print("❌ CSV에 유효한 `id`,`name` 행이 없습니다.")
        input("엔터를 누르면 종료됩니다…")
        return

    # 4) 실제 다운로드 로직 호출
    download_bills_for_organizations(orgs, year=year, month=month)


if __name__ == "__main__":
    main()
    input("완료! 다음 달 업무를 위해 CSV 파일은 삭제하세요. 엔터를 누르면 종료됩니다…")
