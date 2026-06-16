# Eval Data Layout

`evals.json` contains the local regression prompts used for this skill. The
current prompts reference the author's local fixture directory:

`C:\Users\Yanan.Liu\Desktop\3.2.S.7.3 申报资料智能体测试范例-真实数据`

When running these evals on another machine, keep the prompt wording and
assertions unchanged, but rewrite the project root paths to that machine's
fixture root before launching the runs. Do not package fixture data or generated
outputs inside this skill directory.

Required fixture subdirectories:

- `3.2.S.7.3-IP350`
- `3.2.S.7.3-IP319`
- `3.2.S.7.3-IP315`
- `3.2.S.7.3-IP248A`
- `3.2.S.7.3-IP177`
