import { readFileSync } from "node:fs";

const { case: input, prompts } = JSON.parse(readFileSync(0, "utf8"));
const output = { answer: `${prompts["prompts/system.md"].trim()} ${input.question}!` };
process.stdout.write(JSON.stringify(output) + "\n");
