import { ItemView } from "../../components/Item";
import { Badge, Card, Row, Stack } from "../../components/ui";
import type { TabProps } from "../Exam";

const TYPE: Record<string, string> = { multiple_choice: "선택형", short_answer: "단답형", essay: "서술형" };

export function QuestionsTab({ detail }: TabProps) {
  return (
    <Stack>
      {detail.exam.questions.map((q) => (
        <Card
          key={q.number}
          title={
            <Row>
              문항 {q.number}
              <Badge>{TYPE[q.item.item_type]}</Badge>
              <Badge>{q.points}점</Badge>
              {q.item.figures.length > 0 && <Badge>그림 {q.item.figures.length}</Badge>}
            </Row>
          }
        >
          <ItemView number={q.number} points={q.points} item={q.item} examId={detail.exam.id} />
        </Card>
      ))}
    </Stack>
  );
}
