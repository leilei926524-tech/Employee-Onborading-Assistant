"""
飞书机器人集成脚本
基于RAGFlow的新员工入职智能助手
"""

import json
import requests
from flask import Flask, request, jsonify
import hashlib
import base64
import os
from datetime import datetime

app = Flask(__name__)

# 配置信息（需要在飞书开放平台获取）
FEISHU_APP_ID = os.getenv('FEISHU_APP_ID', 'your_app_id')
FEISHU_APP_SECRET = os.getenv('FEISHU_APP_SECRET', 'your_app_secret')
FEISHU_VERIFICATION_TOKEN = os.getenv('FEISHU_VERIFICATION_TOKEN', 'your_token')
FEISHU_ENCRYPT_KEY = os.getenv('FEISHU_ENCRYPT_KEY', '')

# RAGFlow配置
RAGFLOW_API_ENDPOINT = os.getenv('RAGFLOW_API_ENDPOINT', 'http://localhost:8080')
RAGFLOW_API_TOKEN = os.getenv('RAGFLOW_API_TOKEN', 'your_ragflow_token')
RAGFLOW_KNOWLEDGE_BASE_ID = os.getenv('RAGFLOW_KB_ID', 'kb_001')


class FeishuAPI:
    """飞书API封装"""

    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None

    def get_tenant_access_token(self):
        """获取tenant_access_token"""
        url = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
        headers = {'Content-Type': 'application/json'}
        data = {
            'app_id': self.app_id,
            'app_secret': self.app_secret
        }

        try:
            response = requests.post(url, headers=headers, json=data)
            result = response.json()

            if result.get('code') == 0:
                self.access_token = result['tenant_access_token']
                return self.access_token
            else:
                print(f"获取token失败: {result}")
                return None
        except Exception as e:
            print(f"获取token异常: {str(e)}")
            return None

    def send_message(self, receive_id, msg_type, content):
        """发送消息"""
        if not self.access_token:
            self.get_tenant_access_token()

        url = 'https://open.feishu.cn/open-apis/im/v1/messages'
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        params = {'receive_id_type': 'open_id'}

        data = {
            'receive_id': receive_id,
            'msg_type': msg_type,
            'content': json.dumps(content)
        }

        try:
            response = requests.post(url, headers=headers, params=params, json=data)
            return response.json()
        except Exception as e:
            print(f"发送消息异常: {str(e)}")
            return None

    def send_text(self, open_id, text):
        """发送文本消息"""
        content = {'text': text}
        return self.send_message(open_id, 'text', content)

    def send_card(self, open_id, title, content, sources=None):
        """发送卡片消息"""
        elements = [
            {
                "tag": "div",
                "text": {
                    "content": content,
                    "tag": "plain_text"
                }
            }
        ]

        # 添加来源信息
        if sources:
            elements.append({
                "tag": "hr"
            })
            elements.append({
                "tag": "div",
                "text": {
                    "content": "📚 参考来源：",
                    "tag": "plain_text"
                }
            })
            for source in sources:
                elements.append({
                    "tag": "div",
                    "text": {
                        "content": f"📄 {source['file']} (第{source['page']}页)",
                        "tag": "plain_text"
                    }
                })

        card = {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "content": f"🤖 {title}",
                    "tag": "plain_text"
                },
                "template": "blue"
            },
            "elements": elements
        }

        return self.send_message(open_id, 'interactive', card)


class RAGFlowAPI:
    """RAGFlow API封装"""

    def __init__(self, endpoint, token, kb_id):
        self.endpoint = endpoint
        self.token = token
        self.kb_id = kb_id

    def query(self, question):
        """查询知识库"""
        url = f"{self.endpoint}/api/v1/chats_openai/{self.kb_id}/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }

        data = {
            'messages': [
                {'role': 'user', 'content': question}
            ],
            'stream': False
        }

        try:
            response = requests.post(url, headers=headers, json=data)
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                answer = result['choices'][0]['message']['content']
                sources = self._extract_sources(result)
                return {
                    'success': True,
                    'answer': answer,
                    'sources': sources
                }
            else:
                return {
                    'success': False,
                    'error': '未找到相关答案'
                }
        except Exception as e:
            print(f"RAGFlow查询异常: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def _extract_sources(self, result):
        """提取来源信息"""
        sources = []
        # 这里需要根据实际的RAGFlow API响应格式来提取
        # 示例格式
        if 'references' in result:
            for ref in result['references']:
                sources.append({
                    'file': ref.get('file_name', '未知文档'),
                    'page': ref.get('page_number', '?')
                })
        return sources


# 初始化API客户端
feishu = FeishuAPI(FEISHU_APP_ID, FEISHU_APP_SECRET)
ragflow = RAGFlowAPI(RAGFLOW_API_ENDPOINT, RAGFLOW_API_TOKEN, RAGFLOW_KNOWLEDGE_BASE_ID)


@app.route('/webhook', methods=['POST'])
def webhook():
    """处理飞书事件回调"""
    data = request.json

    # 验证URL（首次配置时）
    if 'challenge' in data:
        return jsonify({'challenge': data['challenge']})

    # 验证token
    if data.get('header', {}).get('token') != FEISHU_VERIFICATION_TOKEN:
        return jsonify({'error': 'Invalid token'}), 403

    # 处理事件
    event = data.get('event', {})
    event_type = data.get('header', {}).get('event_type')

    if event_type == 'im.message.receive_v1':
        handle_message(event)

    return jsonify({'success': True})


def handle_message(event):
    """处理消息事件"""
    msg_type = event.get('message', {}).get('message_type')

    # 只处理文本消息
    if msg_type != 'text':
        return

    # 提取消息内容
    content = json.loads(event.get('message', {}).get('content', '{}'))
    question = content.get('text', '').strip()

    if not question:
        return

    # 获取发送者信息
    sender_id = event.get('sender', {}).get('sender_id', {}).get('open_id')

    if not sender_id:
        return

    print(f"[{datetime.now()}] 收到问题: {question} from {sender_id}")

    # 发送"正在思考"的提示
    feishu.send_text(sender_id, "🤔 正在查询知识库，请稍候...")

    # 查询RAGFlow
    result = ragflow.query(question)

    if result['success']:
        # 发送答案卡片
        feishu.send_card(
            sender_id,
            "智能助手回答",
            result['answer'],
            result.get('sources', [])
        )
    else:
        # 发送错误信息
        feishu.send_text(
            sender_id,
            f"❌ 抱歉，查询失败：{result.get('error', '未知错误')}\n\n您可以尝试：\n1. 换一种方式提问\n2. 联系HR同事获取帮助"
        )

    print(f"[{datetime.now()}] 已回复 {sender_id}")


@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'ragflow_endpoint': RAGFLOW_API_ENDPOINT
    })


@app.route('/test', methods=['POST'])
def test():
    """测试接口"""
    question = request.json.get('question', '公司的考勤制度是什么？')

    result = ragflow.query(question)

    return jsonify(result)


if __name__ == '__main__':
    print("""
    ╔════════════════════════════════════════════════════╗
    ║   新员工入职智能助手 - 飞书机器人                  ║
    ║   Powered by RAGFlow                               ║
    ╚════════════════════════════════════════════════════╝

    配置信息：
    - 飞书App ID: {app_id}
    - RAGFlow地址: {ragflow}
    - 知识库ID: {kb_id}

    服务已启动，等待飞书事件...
    访问 http://localhost:5000/health 查看状态
    """.format(
        app_id=FEISHU_APP_ID,
        ragflow=RAGFLOW_API_ENDPOINT,
        kb_id=RAGFLOW_KNOWLEDGE_BASE_ID
    ))

    app.run(host='0.0.0.0', port=5000, debug=True)
