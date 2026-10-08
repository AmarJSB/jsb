# jsb

## Ponytail (selalu aktif)

Setiap tugas coding di repo ini pakai skill **ponytail**, level default **full**.

@.claude/skills/ponytail/SKILL.md

Perintah: `/ponytail lite|full|ultra`, `/ponytail-review`, `/ponytail-audit`,
`/ponytail-debt`, `/ponytail-gain`, `/ponytail-help`. Matikan: "stop ponytail".

### Kalau bentrok, aturan Amar menang

- Urutan prioritas: stabilitas & correctness, lalu performa, baru ringkas.
  Ponytail mengecilkan solusi, bukan safety.
- Cleanup anti-leak bukan boilerplate: Disconnect RBXScriptConnection, cancel
  loop task.delay/task.spawn, bersihin cache per Player/Instance, unsubscribe
  di OnDisable/OnDestroy, pooling. Jangan dihapus atau di-skip demi diff pendek.
- Format jawaban yang Amar minta (Temuan, Patch strategy, Kode final, Checklist
  uji, Trade-off) tetap dipakai; itu penjelasan yang diminta, bukan debt.

Sumber skill: github.com/DietrichGebert/ponytail @ 552acd5 (v4.13.0, MIT),
disalin tanpa diubah ke `.claude/skills/`. Update: salin ulang folder `skills/`.
