"""真实 smolagents Model 协议的确定性实现；仅替换远程推理，不模拟执行器。"""
from smolagents import Model
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, MessageRole
from smolagents.monitoring import TokenUsage


class FakeModel(Model):
    def __init__(self, script):
        super().__init__(model_id='deterministic-fake-model')
        self.script = script
        self.position = 0

    def generate(self, messages, **kwargs):
        if self.position >= len(self.script):
            action = ('final_answer', {'answer': '脚本结束，申请验收'})
        else:
            action = self.script[self.position]
        self.position += 1
        batch = action if isinstance(action, list) else [action]
        calls = [ChatMessageToolCall(id=f'call-{self.position}' + (f'-{i}' if len(batch) > 1 else ''),
                                    type='function', function=ChatMessageToolCallFunction(name=name, arguments=args))
                 for i, (name, args) in enumerate(batch)]
        return ChatMessage(role=MessageRole.ASSISTANT, content='按计划执行下一步。', tool_calls=calls,
                           token_usage=TokenUsage(input_tokens=10, output_tokens=10))
