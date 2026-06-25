"""验证工作流步骤生命周期辅助函数的状态变更。"""

import unittest

from app.models.task import TaskRun
from app.workflows.step_lifecycle import create_checkpoint, create_run_event, mark_step_completed, mark_step_failed, mark_step_started


class StepLifecycleHelperTests(unittest.TestCase):
    """覆盖步骤启动、完成、失败以及检查点生成逻辑。"""

    def test_mark_step_started_updates_runtime(self) -> None:
        """验证步骤开始后会创建运行时并累加尝试次数。"""
        task_run = TaskRun(run_id='run-1')

        runtime = mark_step_started(task_run, 'retrieve_evidence')

        self.assertEqual(task_run.current_step_id, 'retrieve_evidence')
        self.assertEqual(task_run.step_attempts['retrieve_evidence'], 1)
        self.assertEqual(runtime.status, 'running')
        self.assertEqual(runtime.attempt_count, 1)
        self.assertIsNotNone(runtime.started_at)
        self.assertIsNone(runtime.completed_at)

    def test_mark_step_completed_updates_runtime_and_completed_ids(self) -> None:
        """验证步骤完成后会记录完成状态和已完成步骤列表。"""
        task_run = TaskRun(run_id='run-1')
        mark_step_started(task_run, 'analyze')

        runtime = mark_step_completed(task_run, 'analyze', completed_step_ids=['analyze'])

        self.assertEqual(task_run.completed_step_ids, ['analyze'])
        self.assertEqual(runtime.status, 'completed')
        self.assertEqual(runtime.exit_reason, 'completed')
        self.assertIsNotNone(runtime.completed_at)

    def test_mark_step_failed_preserves_attempt_count(self) -> None:
        """验证步骤失败时仍保留本次尝试次数和当前步骤指针。"""
        task_run = TaskRun(run_id='run-1')
        mark_step_started(task_run, 'draft_artifact')

        runtime = mark_step_failed(task_run, 'draft_artifact', completed_step_ids=[], error='boom')

        self.assertEqual(runtime.status, 'failed')
        self.assertEqual(runtime.exit_reason, 'boom')
        self.assertEqual(runtime.attempt_count, 1)
        self.assertEqual(task_run.current_step_id, 'draft_artifact')

    def test_create_checkpoint_and_run_event(self) -> None:
        """验证检查点和运行事件会保留必要的路由与载荷信息。"""
        checkpoint = create_checkpoint(
            step_id='review_artifact',
            next_route='finalize',
            completed_step_ids=['analyze', 'draft_artifact'],
            state_snapshot={'task_id': 'task-1'},
        )
        event = create_run_event('workflow_step_completed', {'task_id': 'task-1'})

        self.assertEqual(checkpoint.step_id, 'review_artifact')
        self.assertEqual(checkpoint.next_route, 'finalize')
        self.assertEqual(checkpoint.completed_step_ids, ['analyze', 'draft_artifact'])
        self.assertEqual(event.name, 'workflow_step_completed')
        self.assertEqual(event.payload['task_id'], 'task-1')


if __name__ == '__main__':
    unittest.main()
