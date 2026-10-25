#!/usr/bin/env bash
# Turn a raw screen recording into a YouTube-ready 1080p MP4 with an intro and an outro card.
#   ./video/make_video.sh path/to/raw.mov [out.mp4]
# Uses only ffmpeg. Colours are the app's dark-theme tokens.
set -euo pipefail
RAW="${1:?usage: make_video.sh raw.mov [out.mp4]}"
OUT="${2:-video/probation-demo.mp4}"
REPO_URL="${REPO_URL:-github.com/your-team/probation}"
FONT_B="/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_R="/System/Library/Fonts/Supplemental/Arial.ttf"
BG="0x111714"; INK="0xe7eee9"; MUTED="0xa3b2a9"; ACCENT="0x86aef0"; GOOD="0x67c48a"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

svg_card() {  # svg_card <png> <svg-body>
  cat > "$TMP/card.svg" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <rect width="1920" height="1080" fill="#111714"/>
  <g font-family="Arial, Helvetica, sans-serif" text-anchor="middle">$2</g>
</svg>
SVG
  rsvg-convert -w 1920 -h 1080 "$TMP/card.svg" -o "$1"
}
clip() {  # clip <png> <seconds> <out.mp4>
  ffmpeg -loglevel error -y -loop 1 -framerate 30 -t "$2" -i "$1" -f lavfi -t "$2" -i "anullsrc=r=48000:cl=stereo" \
    -vf "fade=t=in:st=0:d=0.4,fade=t=out:st=$(echo "$2 - 0.4" | bc):d=0.4,format=yuv420p" \
    -c:v libx264 -preset medium -crf 18 -c:a aac -b:a 192k -shortest "$3"
}
esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

svg_card "$TMP/intro.png" "
  <rect x='880' y='250' width='160' height='12' rx='6' fill='#e7eee9'/><rect x='880' y='274' width='100' height='12' rx='6' fill='#67c48a'/>
  <text x='960' y='420' font-size='120' font-weight='700' fill='#e7eee9'>Probation</text>
  <text x='960' y='520' font-size='46' fill='#86aef0'>Hiring an AI agent the way you hire a person</text>
  <text x='960' y='600' font-size='34' fill='#a3b2a9'>Pip · the accounts-payable specialist at Harborline Provisions</text>
  <text x='960' y='800' font-size='30' fill='#67c48a'>Cal Hacks 13.0</text>"
svg_card "$TMP/outro.png" "
  <text x='960' y='400' font-size='68' font-weight='700' fill='#e7eee9'>The model proposes. Code decides.</text>
  <text x='960' y='500' font-size='34' fill='#a3b2a9'>Limits in code · tamper-evident record · trust earned by rules · learns from its mistakes</text>
  <text x='960' y='560' font-size='34' fill='#a3b2a9'>Real ERPNext record · synthetic company · every number from make eval</text>
  <text x='960' y='700' font-size='42' fill='#86aef0'>$(esc "$REPO_URL")</text>"
clip "$TMP/intro.png" 4.5 "$TMP/intro.mp4"
clip "$TMP/outro.png" 6 "$TMP/outro.mp4"

# main recording: fit into 1920x1080, 30 fps, loudness-normalised audio (silence if the recording has none)
if ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$RAW" | grep -q .; then AIN=(-i "$RAW"); AMAP="0:a"
else AIN=(-i "$RAW" -f lavfi -i "anullsrc=r=48000:cl=stereo"); AMAP="1:a"; fi
ffmpeg -loglevel error -y "${AIN[@]}" -map 0:v -map "$AMAP" -shortest \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=${BG},fps=30,format=yuv420p" \
  -af "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000" -ac 2 \
  -c:v libx264 -preset medium -crf 18 -c:a aac -b:a 192k "$TMP/main.mp4"

printf "file '%s'\n" "$TMP/intro.mp4" "$TMP/main.mp4" "$TMP/outro.mp4" > "$TMP/list.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$TMP/list.txt" -c copy -movflags +faststart "$OUT"
echo "wrote $OUT ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" | cut -d. -f1)s)"
