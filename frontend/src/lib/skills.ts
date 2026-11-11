export const SKILL_LABEL: Record<string, string> = {
  "ai-foundations": "AI Foundations",
  "mgmt-people": "People", "mgmt-execution": "Execution", "mgmt-strategy": "Strategy", "mgmt-finance": "Finance",
  "mgmt-product-gtm": "Product", "mgmt-operations": "Operations", "mgmt-communication": "Comms", "mgmt-governance": "Governance",
  pytorch: "PyTorch", "classical-ml": "Classical ML", rag: "RAG", "vector-db": "Vector DB",
  "llm-finetuning": "Fine-tuning", mlops: "MLOps", "llm-evals": "Evals",
};
export const skillLabel = (slug: string) => SKILL_LABEL[slug] ?? slug;

/** A thinking/writing exercise (no code to run): answers are typed straight into the submission box, no files needed. */
export const isWrittenExercise = (spec: { steps: { commands: string[] }[]; project_layout: string[] }) =>
  spec.steps.every((s) => s.commands.length === 0) && spec.project_layout.every((f) => /\.(md|txt)$/i.test(f));
