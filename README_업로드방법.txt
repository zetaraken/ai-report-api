AI매출업 FastAPI 백엔드 서버 Railway 배포 안내

1. 이 ZIP 파일 압축을 풉니다.
2. GitHub에서 새 저장소를 만듭니다. 예: ai-report-api
3. 압축 푼 파일 전체를 업로드합니다.
4. Railway에서 New Project > GitHub Repository 선택
5. ai-report-api 저장소 선택
6. Railway가 자동 배포합니다.
7. Railway Variables에 아래 환경변수를 등록합니다.

ADMIN_EMAIL=zetarise@gmail.com
ADMIN_PASSWORD=원하는비밀번호
JWT_SECRET_KEY=ai-report-secret-2026-long-random

8. Railway에서 Public Networking / Generate Domain으로 URL을 만듭니다.
9. 생성된 URL을 Vercel의 VITE_API_BASE_URL 환경변수에 넣습니다.

확인 주소:
https://생성된주소/
https://생성된주소/api/health
