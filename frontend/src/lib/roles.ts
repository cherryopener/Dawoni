// 한 번에 채점: 놓은 파일의 역할 짐작 (QuickStart). 채팅에 파일을 올리듯 한꺼번에 놓아도 되게.

export type Role = "exam" | "answers" | "roster" | "skip";

/** HWP·HWPX·DOCX = 시험지, 엑셀·CSV = 명단, PDF는 이름에 시험·해설·문제·정답이 있고 아직 시험지가 없으면
 * 시험지, 아니면 학생 답안지 (강사가 바꿀 수 있다). */
export function guessRole(name: string, haveExam: boolean): Role {
  const n = name.toLowerCase();
  if (/\.(hwp|hwpx|docx)$/.test(n)) return "exam";
  if (/\.(xlsx|xls|csv)$/.test(n)) return "roster";
  if (!/\.pdf$/.test(n)) return "skip";
  const looksExam = /(시험|해설|문제|정답|exam|key)/.test(n) && !/(답안|채점)/.test(n);
  return !haveExam && looksExam ? "exam" : "answers";
}
