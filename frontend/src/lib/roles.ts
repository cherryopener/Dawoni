// 한 번에 채점: 놓은 파일의 역할 짐작 (QuickStart). 채팅에 파일을 올리듯 한꺼번에 놓아도 되게.

export type Role = "exam" | "answers" | "roster" | "skip";

/** HWP·HWPX·DOCX = 시험지, 엑셀·CSV·사진 = 명단, PDF는 이름에 시험·해설·문제·정답이 있고 아직 시험지가 없으면
 * 시험지, 아니면 학생 답안지 (강사가 바꿀 수 있다). */
export function guessRole(name: string, haveExam: boolean): Role {
  const n = name.toLowerCase();
  if (/\.(hwp|hwpx|docx)$/.test(n)) return "exam";
  if (/\.(xlsx|xls|csv|jpg|jpeg|png)$/.test(n)) return "roster";
  if (!/\.pdf$/.test(n)) return "skip";
  const looksExam = /(시험|해설|문제|정답|exam|key)/.test(n) && !/(답안|채점)/.test(n);
  return !haveExam && looksExam ? "exam" : "answers";
}

const loose = (s: string) => s.toLowerCase().replace(/반$/, "").replace(/[^0-9a-z가-힣]/g, "");

/** 파일 이름에 명부의 반 이름이 들어 있으면 그 반 ("MSS1-J 답안.pdf" → MSS1-J). 없거나 둘 이상이면 "". */
export function guessClass(fileName: string, classes: string[]): string {
  const name = loose(fileName.replace(/\.[^.]+$/, ""));
  const hits = classes.filter((c) => loose(c).length >= 2 && name.includes(loose(c)));
  return hits.length === 1 ? hits[0]! : "";
}
