/** 문제은행 화면들이 함께 쓰는 이름표·주소. */

export const bankFileUrl = (path: string) => `/api/bank/file?path=${encodeURIComponent(path)}`;

export const SOURCE_KIND: Record<string, string> = {
  problem_bank: "문제집·강사 교재",
  textbook: "교과서·개념서",
  mock_exam: "기출·모의고사",
  dawoni: "다워니 제작",
};
