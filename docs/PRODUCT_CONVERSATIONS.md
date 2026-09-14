# Customer and staff conversations

The customer sees concise business answers. A final presentation call receives the current workflow result and application status, and produces a short Chinese reply. It is instructed to omit internal field names, review scores, risk labels, tool names, and implementation details. Additional checks reject selected internal terms and unsupported completed-refund claims. These checks reduce risk; they do not constitute a complete hallucination detector.

The staff console shows customer-visible history, order facts, application evidence, editable reply suggestions, and approve/reject controls for an existing pending application. Staff sending a message switches that conversation to manual handling. Subsequent customer messages are delivered to staff without running the Agent. Restoring automatic handling includes recent visible conversation history in the next request.

Messages and handling mode persist in `data/support_conversations.db`. Polling delivers staff messages to the customer page. Review endpoints require the existing review token; the customer experience remains a fixed local demonstration account and requires production identity integration before public deployment.

Attention hints are transparent keyword signals from the latest customer message. They are not a trained sentiment classifier. AI reply suggestions are drafts; staff must review and send them explicitly. The interface currently accepts text, not evidence attachments.

## Focused verification, 2026-09-13

- AIHubMix request model: `coding-glm-5-free`; returned model: `glm-5.3`.
- A real `/customer/chat` request asking whether a delayed order had arrived produced a Chinese response with the recorded delivery date and 11-day delay. Internal category codes and review score were omitted.
- A staff message was persisted and retrieved through the customer messages endpoint. A subsequent customer reply while manual mode was enabled returned acknowledgment and was recorded without invoking the Agent.
- A real suggestion request generated a draft. The first draft incorrectly assumed an upload channel; the prompt was then constrained to the available text-only channel. This is a known failure example, not an aggregate benchmark score.
- Three targeted tests cover message/mode persistence, isolation between sessions, and final-presentation filtering. No full benchmark rerun was performed.
