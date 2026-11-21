# Liquid

Bentuk-bentuk yang meleleh menjadi satu, meregang sebelum putus, lalu memantul kembali. Permukaannya mengkilap, tepinya organik, dan tidak ada satu garis lurus pun sepanjang episode.

**Asal:** [MG Styles 15](https://github.com/Vincentwei1021/mg-styles-15) `demos/07-liquid`, commit `8fab7eb`, MIT. Aturan visual diambil dari `prompts/07-liquid.md`; kodenya ditulis ulang untuk HyperFrames.

## Kapan dipakai

Peluncuran produk, musik, atau transisi yang harus terasa bernergi. Juga bagus untuk membuka episode yang topiknya abstrak, karena liquidad memberi alasan visual untuk perubahan bentuk tanpa harus dijelaskan.

## Yang harus ada

- **Metaball tanpa WebGL.** Tiga lingkaran diblur 22 px lalu di-threshold lewat `feColorMatrix` alpha (`0 0 0 26 -12`). Saat berdekatan, keduanya menyatu sendiri — evaluasi dilakukan GPU filter, bukan JS.
- **Leher meregang lalu putus.** Elips menjauh dengan `power3.in`, lalu memantul balik dengan `elastic.out`.
- **Front gelombang** yang tersusun dari beberapa bulge dengan fase berbeda, bukan satu kurva lurus.
- **Drip** yang jatuh dan mendatar saat mendarat (squash 2.4× lebar).
- **Squash saat mendarat**, sebelum bentuknya mengendap.

## Palet

Latar `#241233` plum dalam, kecerahan `#FF7A3D` oranye → `#FF3D8B` pink → `#8A4BFF` ungu pada gradien kandy. Teks `#FFEDD5`. Gradien di sini memang dipakai — kebalikan dari gaya flat — karena glossy adalah intinya.

## Gerak

`power4.out` sebagai pengganti `cubic-bezier(.22,1,.36,1)`: dilempar, bukan digeser. Droplet sekunder menyusul 2–3 frame lebih lambat.

## Jangan

- Jangan membuat tepi lurus. Satu garis tegas merusak seluruh permukaan.
- Jangan pakai gerak merata. Cairan selalu dipercepat lalu mengendap.
- Jangan threshold terlalu agresif — nanti bentuknya pecah jadi bintik.

## Signature

- `feGaussianBlur` — Blur gaussian sebagai langkah pertama metaball.
- `feColorMatrix` — Threshold alpha lewat feColorMatrix, sehingga blob menyatu.
- `waveFront` — Front transisi berupa gelombang dengan bulge berfase berbeda.
- `drip` — Tetesan jatuh dan mendatar saat mendarat.
- `flung` — Easing dilempar (power4.out), bukan digeser merata.
