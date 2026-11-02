type Candidate = {
  name?: string; mode?: string; graph?: unknown; parameter_map?: unknown;
  media_transfers?: unknown; required_nodes?: unknown;
};

export default function WorkflowProfileImport({ onLoad }: { onLoad: (candidate: Candidate) => void }) {
  async function load(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const candidate = JSON.parse(await file.text()) as Candidate;
      if (!candidate || typeof candidate !== 'object' || !candidate.graph || !candidate.parameter_map) throw new Error();
      onLoad(candidate);
    } catch {
      window.alert('无法读取候选档案：请使用包含 API 图和参数映射的 JSON。');
    } finally {
      event.target.value = '';
    }
  }
  return <label>导入候选 Profile JSON（导入后仍需检查并验证）
    <input type="file" accept=".json,application/json" onChange={load} />
  </label>;
}
