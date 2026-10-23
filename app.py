import base64, json, os, re, random, subprocess, tempfile, threading, time, uuid, shutil, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from datetime import date as _date, timedelta
from flask import Flask, request, jsonify, send_file, render_template, abort
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
BASE = ROOT
ASSETS = ROOT / 'assets'
OUT = ROOT / 'outputs'
OUT.mkdir(exist_ok=True)
W, H, FPS = 1080, 1920, 25
FONT_PATH = ASSETS / 'Helvetica Condensed Bold Italic.ttf'
BG_PATH = ASSETS / 'background.jpg'
MID90S_BG_PATH = ASSETS / 'mid90s_loop_background.mp4'
PRINTER_SFX_PATH = ASSETS / 'printer_typing.wav'
PRINTER_TITLE_FONT_PATH = ASSETS / 'Helvetica Neue Condensed Bold.ttf'
BOX_PATH = ASSETS / 'result_box_row.png'
IDENT_PATH = ASSETS / 'identity.png'
HEADER_PATH = ASSETS / 'league_header.png'
STUDIO_LOGO_PATH = ASSETS / 'grandscores_studio_logo.png'
TABLE_BG_PATH=ASSETS/'league_background.jpg'
TABLE_DETAIL_PATH=ASSETS/'league_table_detail.png'
TABLE_ROW_PATH=ASSETS/'league_row.png'
TABLE_ORANGE_ROW_PATH=ASSETS/'league_orange_row.png'
TABLE_WHITE_ROW_PATH=ASSETS/'league_white_row.png'
TABLE_BG=Image.open(TABLE_BG_PATH).convert('RGBA')
TABLE_DETAIL=Image.open(TABLE_DETAIL_PATH).convert('RGBA')
TABLE_ROW=Image.open(TABLE_ROW_PATH).convert('RGBA')
TABLE_ORANGE_ROW=Image.open(TABLE_ORANGE_ROW_PATH).convert('RGBA')
TABLE_WHITE_ROW=Image.open(TABLE_WHITE_ROW_PATH).convert('RGBA')
TABLE_ROW_CROP=TABLE_ROW.crop((65,541,1035,649))
TABLE_ORANGE_CROP=TABLE_ORANGE_ROW.crop((65,611,1035,719))
TABLE_WHITE_CROP=TABLE_WHITE_ROW.crop((65,611,1035,719))
TABLE_LOGO_CROP=TABLE_DETAIL.crop((500,1200,1030,1380))
TABLE_DETAIL_NO_LOGO=TABLE_DETAIL.copy()
_alpha=TABLE_DETAIL_NO_LOGO.getchannel('A')
for _y in range(1200,1380):
    for _x in range(500,1030): _alpha.putpixel((_x,_y),0)
TABLE_DETAIL_NO_LOGO.putalpha(_alpha)

FONT_CACHE = {}
RESULT_FONT_SIZE = 42
MAX_TEAM_CHARS = 20
APP_VERSION = '5.40'
PREVIEW_MAX_AGE = 300

def font(size):
    if size not in FONT_CACHE:
        FONT_CACHE[size] = ImageFont.truetype(str(FONT_PATH), size)
    return FONT_CACHE[size]

BG = Image.open(BG_PATH).convert('RGBA')
BOX = Image.open(BOX_PATH).convert('RGBA')
IDENT = Image.open(IDENT_PATH).convert('RGBA')
HEADER = Image.open(HEADER_PATH).convert('RGBA')
IDENT_CROP = IDENT.crop((555,1222,986,1307))

JOBS = {}
LOCK = threading.Lock()
RENDER_LOCK = threading.Lock()

SAMPLE = {
    'competition': 'Premier League',
    'results': [
        {'home':'Nottingham Forest','homeScore':1,'away':'Chelsea','awayScore':2},
        {'home':'Huddersfield Town','homeScore':2,'away':'Middlesbrough','awayScore':2},
        {'home':'Charlton Athletic','homeScore':1,'away':'Hull City','awayScore':0},
        {'home':'Luton Town','homeScore':0,'away':'Norwich City','awayScore':0},
        {'home':'Arsenal','homeScore':3,'away':'Rochdale United','awayScore':1},
        {'home':'Birmingham City','homeScore':1,'away':'Manchester United','awayScore':2},
        {'home':'Mansfield Town','homeScore':2,'away':'Liverpool','awayScore':2},
        {'home':'Stevenage','homeScore':0,'away':'Aston Villa','awayScore':1},
        {'home':'York City','homeScore':None,'away':'Everton','awayScore':None},
    ]
}

# Pre-compose only the artwork that never changes. Result boxes are added
# per actual result so there are never blank result rows in the finished video.
STATIC = BG.copy()
STATIC_RGB = STATIC.convert('RGB')
STATIC_WITH_IDENTITY = STATIC.copy()
STATIC_WITH_IDENTITY.alpha_composite(IDENT, (0, 0))
STATIC_WITH_IDENTITY_RGB = STATIC_WITH_IDENTITY.convert('RGB')

app = Flask(__name__, template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'static'))




def clean_leagues(data):
    raw=data.get('leagues')
    if raw is None:
        raw=[{'competition':data.get('competition'),'results':data.get('results')}]
    leagues=[]
    for item in raw[:2]:
        results=[]
        for r in (item.get('results') or [])[:12]:
            home=str(r.get('home','')).strip(); away=str(r.get('away','')).strip()
            hs=r.get('homeScore'); aws=r.get('awayScore')
            late = r.get('late') or hs is None or aws is None or (isinstance(hs,str) and hs.strip()=='') or (isinstance(aws,str) and aws.strip()=='')
            def num(v):
                try: return int(v)
                except: return None
            results.append({'home':home,'away':away,'homeScore':None if late else num(hs),'awayScore':None if late else num(aws),'scorers':list(r.get('scorers') or [])})
        leagues.append({'competition':str(item.get('competition') or 'Competition'), 'results':results})
    return {'leagues':leagues,'includeIntroMusic':bool(data.get('includeIntroMusic',True)),'mid90sLoopBackground':bool(data.get('mid90sLoopBackground',False))}

def clean_results(data):
    """Compatibility helper used by the preview endpoint."""
    if data.get('leagues'):
        return clean_leagues(data)['leagues'][0]
    return clean_leagues(data)['leagues'][0]

def score_text(r):
    return 'L-L' if r.get('homeScore') is None or r.get('awayScore') is None else f"{r['homeScore']}-{r['awayScore']}"

def draw_fitted_text(base, text, x, visible_top, fnt, max_width=None):
    if not text: return
    bbox=fnt.getbbox(text)
    px=int(round(x-bbox[0])); py=int(round(visible_top-bbox[1])); d=ImageDraw.Draw(base)
    d.text((px+2,py+2),text,font=fnt,fill=(42,52,76,230)); d.text((px,py),text,font=fnt,fill=(242,242,234,255))

def validate_results(data):
    for li,results_set in enumerate(data['leagues'],1):
        for i,r in enumerate(results_set['results'],1):
            for key,text in [('home',r['home']),('score',score_text(r)),('away',r['away'])]:
                if not text:
                    raise ValueError(f'Results {li}, result {i}: {key} text is empty.')

def animated_background_enabled(data):
    return bool(data.get('mid90sLoopBackground')) and MID90S_BG_PATH.exists()

def make_frame(data, reveal_index=None, progress=1.0, transparent=False):
    results=data['results'][:12]
    base=(Image.new('RGBA',(W,H),(0,0,0,0)) if transparent else STATIC_RGB.copy().convert('RGBA'))
    sections=data.get('sections') or [{'competition':data['competition'],'results':results}]
    combined=bool(data.get('sections'))
    if not combined:
        if data['competition'].strip().lower()=='premier league': base.alpha_composite(HEADER,(0,0))
        else:
            d=ImageDraw.Draw(base); title=data['competition'].strip() or 'Competition'; tf=font(64); tb=tf.getbbox(title); tx=84-tb[0]; ty=427-tb[1]
            d.text((tx+2,ty+2),title,font=tf,fill=(42,52,76,220)); d.text((tx,ty),title,font=tf,fill=(246,244,104,255))
        identity_shift_y=70*(len(results)-9); base.alpha_composite(IDENT,(0,identity_shift_y))
        for i,r in enumerate(results): base.alpha_composite(BOX,(0,541+i*70))
        for i,r in enumerate(results):
            y=541+i*70; rev=1.0 if reveal_index is None else (1.0 if i<reveal_index else (0.0 if i>reveal_index else max(0,min(1,progress))))
            if rev<=0: continue
            row_top=y; f=font(RESULT_FONT_SIZE); sample_bbox=f.getbbox(r['home'] or score_text(r) or r['away']); glyph_h=sample_bbox[3]-sample_bbox[1]; visible_top=row_top+(109-glyph_h)/2-7
            if rev>=1:
                draw_fitted_text(base,r['home'],95,visible_top,f); sc=score_text(r); sw=f.getbbox(sc)[2]-f.getbbox(sc)[0]; draw_fitted_text(base,sc,(473+607)/2-sw/2,visible_top,f); draw_fitted_text(base,r['away'],638,visible_top,f)
            else:
                row_w=1049-75; overlay=Image.new('RGBA',(row_w,109),(0,0,0,0)); draw_fitted_text(overlay,r['home'],20,visible_top-row_top,f); sc=score_text(r); sw=f.getbbox(sc)[2]-f.getbbox(sc)[0]; draw_fitted_text(overlay,sc,(473+607)/2-75-sw/2,visible_top-row_top,f); draw_fitted_text(overlay,r['away'],563,visible_top-row_top,f); reveal_w=int(round(row_w*rev));
                if reveal_w>0: base.alpha_composite(overlay.crop((0,0,reveal_w,109)),(75,row_top))
        return base if transparent else base.convert('RGB')

    # When both result sets contain fewer than four results, combine them onto one page.
    total=sum(len(sec['results']) for sec in sections)
    # Combined small result sets use the same spacing rhythm as a normal
    # single-competition page: 50px between a heading and its first result, and
    # 50px between the last result of one competition and the next heading.
    TITLE_TO_ROW_GAP = 50
    ROW_TO_TITLE_GAP = 50
    TITLE_SIZE = 64
    d=ImageDraw.Draw(base)
    ftitle=font(TITLE_SIZE); first=sections[0]['competition']; bb=ftitle.getbbox(first)
    first_title_draw_y = 427 - bb[1]
    d.text((84-bb[0]+2,first_title_draw_y+2),first,font=ftitle,fill=(42,52,76,220))
    d.text((84-bb[0],first_title_draw_y),first,font=ftitle,fill=(246,244,104,255))
    flat=[]; row_index=0; y=541
    for si,sec in enumerate(sections):
        if si>0:
            title_visible_top = y + ROW_TO_TITLE_GAP + 109 - 70
            hf=font(TITLE_SIZE); h=sec['competition']; hb=hf.getbbox(h)
            title_draw_y = title_visible_top - hb[1]
            d.text((84-hb[0]+2,title_draw_y+2),h,font=hf,fill=(42,52,76,220))
            d.text((84-hb[0],title_draw_y),h,font=hf,fill=(246,244,104,255))
            y = title_visible_top + (hb[3]-hb[1]) + TITLE_TO_ROW_GAP
        for r in sec['results']:
            flat.append((row_index,r,y)); row_index+=1; y+=70
    final_row_bottom = flat[-1][2] + 109 if flat else 541
    identity_top = final_row_bottom + 12
    identity_shift_y = identity_top - IDENT.getbbox()[1]
    base.alpha_composite(IDENT,(0,identity_shift_y))
    for idx,r,y in flat: base.alpha_composite(BOX,(0,y))
    f=font(RESULT_FONT_SIZE)
    for idx,r,y in flat:
        rev=1.0 if reveal_index is None else (1.0 if idx<reveal_index else (0.0 if idx>reveal_index else max(0,min(1,progress))))
        if rev<=0: continue
        glyph_h=f.getbbox(r['home'] or score_text(r) or r['away'])[3]-f.getbbox(r['home'] or score_text(r) or r['away'])[1]; visible_top=y+(109-glyph_h)/2-7
        if rev>=1:
            draw_fitted_text(base,r['home'],95,visible_top,f); sc=score_text(r); sw=f.getbbox(sc)[2]-f.getbbox(sc)[0]; draw_fitted_text(base,sc,(473+607)/2-sw/2,visible_top,f); draw_fitted_text(base,r['away'],638,visible_top,f)
        else:
            row_w=1049-75; overlay=Image.new('RGBA',(row_w,109),(0,0,0,0)); draw_fitted_text(overlay,r['home'],20,visible_top-y,f); sc=score_text(r); sw=f.getbbox(sc)[2]-f.getbbox(sc)[0]; draw_fitted_text(overlay,sc,(473+607)/2-75-sw/2,visible_top-y,f); draw_fitted_text(overlay,r['away'],563,visible_top-y,f); reveal_w=int(round(row_w*rev));
            if reveal_w>0: base.alpha_composite(overlay.crop((0,0,reveal_w,109)),(75,y))
    return base if transparent else base.convert('RGB')

VO_PRONUNCIATIONS={
    'wycombe wanderers':'Wickam Wanderers',
    'reading':'Redding',
    'gillingham':'Jillingham',
    'salford':'Solford',
    'salford city':'Solford City',
    'tottenham':'Totteringham',
    'tottenham hotspur':'Totteringham Hotspur',
    'wolves':'Wolverhampton Wanderers',
    'west brom albion':'West Bromwich Albion',
    'milton keynes dons':'Milton Keens Dons',
    'wigan athletic':'Wiggan Athletic',
}

def vo_team_name(name):
    text=str(name or '')
    return VO_PRONUNCIATIONS.get(text.strip().lower(),text)

def vo_text(text):
    """Apply all configured team-name pronunciation aliases to arbitrary VO text."""
    out=str(text or '')
    # Longest first prevents a shorter alias from pre-empting a longer one.
    for source, replacement in sorted(VO_PRONUNCIATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        out=re.sub(r'(?<!\w)'+re.escape(source)+r'(?!\w)', replacement, out, flags=re.I)
    return out

def script_lines(data):
    lines=[f"{data['competition']} Division."]
    for r in data['results']:
        home=vo_team_name(r['home']); away=vo_team_name(r['away'])
        if r['homeScore'] is None or r['awayScore'] is None: lines.append(f"{home} versus {away}, late result.")
        else:
            def vo_score(value):
                if value == 0: return 'nill'  # Keep football score 0 consistently pronounced as 'nil' /nɪl/ by TTS.
                words = {1:'one',2:'two',3:'three',4:'four',5:'five',6:'six',7:'seven',8:'eight',9:'nine',10:'ten'}
                return words.get(int(value), str(value))
            hs=vo_score(r['homeScore']); aws=vo_score(r['awayScore'])
            lines.append(f"{home}, <break time=\"0.18s\" /> {hs}. <break time=\"0.24s\" /> {away}, <break time=\"0.18s\" /> {aws}.")
    return lines

def combine_small_result_sets(data):
    # Preserve render-level options such as the animated Mid-90s background
    # when normalising the Results payload. Previously this helper rebuilt the
    # dict and accidentally discarded mid90sLoopBackground, so Results could
    # silently fall back to the static background while League Table worked.
    leagues=data.get('leagues') or []
    base={k:v for k,v in data.items() if k != 'leagues'}
    if len(leagues)==2 and len(leagues[0].get('results') or [])<4 and len(leagues[1].get('results') or [])<4 and leagues[0].get('results') and leagues[1].get('results'):
        out=dict(base)
        out.update({'leagues':[{'competition':leagues[0]['competition'], 'results':leagues[0]['results'] + leagues[1]['results'], 'sections':[
            {'competition':leagues[0]['competition'], 'results':leagues[0]['results']},
            {'competition':leagues[1]['competition'], 'results':leagues[1]['results']}
        ]}], 'combined_small_sets':True})
        return out
    out=dict(base)
    out.update({'leagues':leagues, 'combined_small_sets':False})
    return out

def combined_script_lines(league):
    lines=[]
    for section in league.get('sections') or []:
        lines += script_lines({'competition':section['competition'], 'results':section['results']})
    return lines

def elevenlabs_tts(text,path,max_retries=6):
    key=os.getenv('ELEVENLABS_API_KEY'); voice=os.getenv('ELEVENLABS_VOICE_ID'); model=os.getenv('ELEVENLABS_MODEL','eleven_multilingual_v2')
    if not key: raise RuntimeError('ELEVENLABS_API_KEY is not configured in the service environment.')
    if not voice: raise RuntimeError('ELEVENLABS_VOICE_ID is not configured in the service environment.')
    url=f'https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream?output_format=mp3_44100_128'
    payload={'text':text,'model_id':model,'apply_text_normalization':'auto','voice_settings':{'speed':1.0}}
    delay=1.0
    for attempt in range(max_retries+1):
        req=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),headers={'xi-api-key':key,'Content-Type':'application/json','Accept':'audio/mpeg'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=120) as resp: audio=resp.read()
            if not audio: raise RuntimeError('ElevenLabs returned an empty audio file.')
            path.write_bytes(audio); return path
        except urllib.error.HTTPError as e:
            detail=''
            try: detail=e.read().decode('utf-8','replace')
            except Exception: pass
            retryable=e.code in (429,500,502,503,504)
            if retryable and attempt < max_retries:
                # ElevenLabs recommends exponential backoff for 429/5xx responses.
                wait=max(0.5, min(12.0, delay*(0.5+__import__('random').random())))
                time.sleep(wait); delay=min(delay*2,12.0); continue
            raise RuntimeError(f'ElevenLabs TTS failed ({e.code}): {detail[:500]}') from e
        except urllib.error.URLError as e:
            if attempt < max_retries:
                wait=max(0.5, min(12.0, delay*(0.5+__import__('random').random())))
                time.sleep(wait); delay=min(delay*2,12.0); continue
            raise RuntimeError(f'Could not connect to ElevenLabs: {e.reason}') from e

def run_ffmpeg(cmd): return subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)

def audio_duration(path):
    return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],stderr=subprocess.DEVNULL).decode().strip())

def make_audio_multi(data,td,job_id):
    data=combine_small_result_sets(data)
    segments=[]; durations=[]; total_leagues=len(data['leagues'])
    for li,league in enumerate(data['leagues'],1):
        lines=combined_script_lines(league) if league.get('sections') else script_lines(league); clips=[]
        for i,line in enumerate(lines):
            clip=td/f'voice_l{li}_{i:02d}.mp3'; elevenlabs_tts(line,clip); clips.append(clip)
            with LOCK:
                done=sum(len(combined_script_lines(x) if x.get('sections') else script_lines(x)) for x in data['leagues'][:li-1])+i+1; allc=sum(len(combined_script_lines(x) if x.get('sections') else script_lines(x)) for x in data['leagues']); JOBS[job_id]['progress']=3+int(done/max(allc,1)*7); JOBS[job_id]['message']=f'Generating voiceover: Results {li}, clip {i+1} of {len(lines)}…'
        cat=td/f'clips_l{li}.txt'; cat.write_text('\n'.join(f"file '{c.as_posix().replace(chr(39),chr(39)+chr(92)+chr(39)+chr(39))}'" for c in clips),encoding='utf-8')
        voice=td/f'voice_l{li}.mp3'; run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(cat),'-c','copy',str(voice)])
        dur=audio_duration(voice)
        if league.get('sections'):
            visual_results=sum(len(x['results']) for x in league['sections'])
        else: visual_results=len(league['results'])
        pre_frames=4*visual_results+2*max(0,visual_results-1)+25
        pre_ms=round(pre_frames/FPS*1000)
        seg=td/f'audio_segment_{li}.mp3'; run_ffmpeg(['ffmpeg','-y','-loglevel','error','-i',str(voice),'-af',f'adelay={pre_ms}:all=1','-c:a','libmp3lame','-b:a','160k',str(seg)])
        segments.append(seg); durations.append(dur)
    catall=td/'audio_all.txt'; catall.write_text('\n'.join(f"file '{c.as_posix().replace(chr(39),chr(39)+chr(92)+chr(39)+chr(39))}'" for c in segments),encoding='utf-8')
    out=td/'voice_all.mp3'; run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(catall),'-c','copy',str(out)])
    return out,durations,data

def _write_animated_png_frame(frame, path):
    # Low compression keeps rendering fast while preserving the alpha channel.
    frame.save(path, format='PNG', compress_level=1)

def _encode_animated_foreground(frame_dir, out_path, total_frames):
    # The background is already native 1080x1920/25fps. Keep it as a real
    # video stream and overlay the transparent foreground PNG sequence.
    # This avoids pushing ~8 MB of RGBA data per frame through a Python pipe,
    # which was causing FFmpeg to exit and Python to report Broken pipe.
    # Railway/container memory is limited. FFmpeg otherwise defaults several
    # filter graphs to the detected CPU count, which can create many full-size
    # 1080x1920 frame buffers and cause the OS to SIGKILL the render. Constrain
    # filter parallelism and input queues while keeping the background as a
    # continuously looping native video stream.
    cmd=['ffmpeg','-y','-loglevel','error',
         '-filter_threads','1','-filter_complex_threads','1',
         '-thread_queue_size','4','-stream_loop','-1','-i',str(MID90S_BG_PATH),
         '-thread_queue_size','4','-framerate',str(FPS),'-i',str(frame_dir/'%06d.png'),
         '-filter_complex','[0:v][1:v]overlay=0:0:format=auto[v]',
         '-map','[v]','-frames:v',str(total_frames),
         '-c:v','libx264','-preset','ultrafast','-threads','1','-crf','23',
         '-pix_fmt','yuv420p','-movflags','+faststart',str(out_path)]
    run_ffmpeg(cmd)

def stream_video_multi(data,durations,out_path,job_id,extra_tail_seconds=0.0):
    data=combine_small_result_sets(data)
    animated=animated_background_enabled(data)
    def frame_count(league,dur):
        n=sum(len(x['results']) for x in league.get('sections',[])) if league.get('sections') else len(league['results'])
        return 4*n+2*max(0,n-1)+25+int(round(dur*FPS))
    total_frames=sum(frame_count(l,d) for l,d in zip(data['leagues'],durations))+max(0,int(round(extra_tail_seconds*FPS)))
    if animated:
        with tempfile.TemporaryDirectory(prefix='grandscores_results_fg_') as fd:
            frame_dir=Path(fd)
            done_frames=0
            frame_no=1
            for li,(league,dur) in enumerate(zip(data['leagues'],durations),1):
                if league.get('sections'):
                    n=sum(len(x['results']) for x in league['sections'])
                    for i in range(n):
                        for fidx in range(4):
                            frame=make_frame(league,i,(fidx+1)/4,transparent=True)
                            _write_animated_png_frame(frame,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; done_frames+=1
                        if i<n-1:
                            for _ in range(2):
                                frame=make_frame(league,i,1.0,transparent=True)
                                _write_animated_png_frame(frame,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; done_frames+=1
                    final=make_frame(league,n-1,1.0,transparent=True)
                else:
                    n=len(league['results'])
                    for i in range(n):
                        for fidx in range(4):
                            frame=make_frame(league,i,(fidx+1)/4,transparent=True)
                            _write_animated_png_frame(frame,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; done_frames+=1
                        if i<n-1:
                            for _ in range(2):
                                frame=make_frame(league,i,1.0,transparent=True)
                                _write_animated_png_frame(frame,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; done_frames+=1
                    final=make_frame(league,n-1,1.0,transparent=True)
                intro_tail_frames=0
                if li==len(data['leagues']) and extra_tail_seconds>0:
                    intro_tail_frames=int(round((extra_tail_seconds+0.5)*FPS))
                hold_frames=25+max(1,int(round(dur*FPS)))+intro_tail_frames
                for _ in range(hold_frames):
                    _write_animated_png_frame(final,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; done_frames+=1
                del final
                with LOCK: JOBS[job_id]['progress']=10+int(done_frames/max(total_frames,1)*82); JOBS[job_id]['message']=f'Rendering Results {li} of {len(data["leagues"])}…'
            _encode_animated_foreground(frame_dir,out_path,total_frames)
        return

    cmd=['ffmpeg','-y','-loglevel','error','-f','image2pipe','-vcodec','ppm','-framerate',str(FPS),'-i','-','-c:v','libx264','-preset','ultrafast','-threads','1','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(out_path)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        done_frames=0
        for li,(league,dur) in enumerate(zip(data['leagues'],durations),1):
            if league.get('sections'):
                n=sum(len(x['results']) for x in league['sections'])
                for i in range(n):
                    for fidx in range(4):
                        frame=make_frame(league,i,(fidx+1)/4); frame.save(proc.stdin,format='PPM'); del frame; done_frames+=1
                    if i<n-1:
                        for _ in range(2):
                            frame=make_frame(league,i,1.0); frame.save(proc.stdin,format='PPM'); del frame; done_frames+=1
                final=make_frame(league,n-1,1.0)
            else:
                n=len(league['results'])
                for i in range(n):
                    for fidx in range(4):
                        frame=make_frame(league,i,(fidx+1)/4); frame.save(proc.stdin,format='PPM'); del frame; done_frames+=1
                    if i<n-1:
                        for _ in range(2):
                            frame=make_frame(league,i,1.0); frame.save(proc.stdin,format='PPM'); del frame; done_frames+=1
                final=make_frame(league,n-1,1.0)
            intro_tail_frames=0
            if li==len(data['leagues']) and extra_tail_seconds>0: intro_tail_frames=int(round((extra_tail_seconds+0.5)*FPS))
            hold_frames=25+max(1,int(round(dur*FPS)))+intro_tail_frames
            for _ in range(hold_frames): final.save(proc.stdin,format='PPM')
            del final; done_frames+=hold_frames
            with LOCK: JOBS[job_id]['progress']=10+int(done_frames/max(total_frames,1)*82); JOBS[job_id]['message']=f'Rendering Results {li} of {len(data["leagues"])}…'
        proc.stdin.close(); rc=proc.wait()
        if rc!=0: raise RuntimeError('Video encoding failed')
    except Exception:
        try: proc.stdin.close()
        except Exception: pass
        proc.kill(); proc.wait(); raise

def _draw_table_text(base,text,x,top,size=42,fill=(242,242,234,255),shadow=(42,52,76,230)):
    if not text:return
    f=font(size); bb=f.getbbox(str(text)); px=int(x-bb[0]); py=int(top-bb[1]); d=ImageDraw.Draw(base); d.text((px+2,py+2),str(text),font=f,fill=shadow); d.text((px,py),str(text),font=f,fill=fill)

def _table_selected_rows(data, scene):
    rows=list(data.get('rows') or [])
    half=(len(rows)+1)//2
    if scene=='bottom': return rows[half:]
    return rows[:half]

def _draw_table_label(bg, text, underlay_patch=None):
    # The supplied detail artwork contains the word Top baked into this label
    # area. For the bottom scene we must restore what was underneath the detail
    # artwork before drawing Bottom, otherwise the original Top remains visible.
    if underlay_patch is not None:
        bg.paste(underlay_patch, (160,462))
    d=ImageDraw.Draw(bg)
    f=font(42); bb=f.getbbox(text); x=219-(bb[2]-bb[0])/2; y=505-(bb[3]-bb[1])/2
    d.text((int(x),int(y)),text,font=f,fill=(246,244,104,255))

def _draw_table_row_text(bg,row,y,rev=1.0,white=False):
    """Draw one league-table row. The row artwork is already present; only text is revealed."""
    d=ImageDraw.Draw(bg)
    text_fill=(21,16,85,255) if white else (242,242,234,255)
    shadow=(21,24,45,210) if not white else (21,16,85,100)
    # Coordinates match the supplied table master artwork.
    pos_f=font(42); team_f=font(42); stat_f=font(42)
    def draw_reveal(text,x,top,fnt):
        text=str(text)
        bb=fnt.getbbox(text)
        px=int(x-bb[0]); py=int(top-bb[1])
        if rev < 1.0:
            full_w=max(1,bb[2]-bb[0]); clip=max(0,min(full_w,int(full_w*rev)))
            if clip<=0:return
            layer=Image.new('RGBA',bg.size,(0,0,0,0)); ld=ImageDraw.Draw(layer)
            ld.text((px+2,py+2),text,font=fnt,fill=shadow)
            ld.text((px,py),text,font=fnt,fill=text_fill)
            crop=layer.crop((px,0,min(bg.width,px+clip+4),bg.height))
            bg.alpha_composite(crop,(px,0))
        else:
            d.text((px+2,py+2),text,font=fnt,fill=shadow)
            d.text((px,py),text,font=fnt,fill=text_fill)
    # Text is deliberately inset from the artwork edges.
    # Locked to the supplied alignment guide artwork:
    # position starts at x=98, team names at x=171, played is right-anchored
    # within the team box, PLD ends on the x=860 guide, and PTS ends on
    # the x=1040 guide. The y offset is deliberately unchanged from 3.8
    # because it already places the text on the supplied horizontal guides.
    draw_reveal(row.get('position',''),98,y+25,pos_f)
    draw_reveal(row.get('name',''),196,y+25,team_f)
    played=str(row.get('played',0)); pbb=stat_f.getbbox(played); draw_reveal(played,775,y+25,stat_f)
    points=str(row.get('points',0)); qbb=stat_f.getbbox(points); draw_reveal(points,928,y+25,stat_f)

def _table_row_style(data, position):
    """Return the artwork style for a league-table position."""
    name=str(data.get('name') or '').strip().lower()
    league_id=int(data.get('league_id') or 0) if str(data.get('league_id','')).isdigit() else 0
    position=int(position or 0)
    total=len(data.get('rows') or [])
    # Existing Premier League rule.
    if (league_id == 8 or name == 'premier league') and position in {18,19,20}:
        return 'white'
    # 24-team Championship: 1-2 white, 3-8 orange, 22-24 white.
    if (league_id == 9 or name == 'championship') and total == 24:
        if position in {1,2,22,23,24}: return 'white'
        if 3 <= position <= 8: return 'orange'
    # 24-team League One: 1-2 white, 3-6 orange, 21-24 white.
    if (league_id == 12 or name == 'league one') and total == 24:
        if position in {1,2,21,22,23,24}: return 'white'
        if 3 <= position <= 6: return 'orange'
    # 24-team League Two: 1-3 white, 4-7 orange, 23-24 white.
    if (league_id == 14 or name == 'league two') and total == 24:
        if position in {1,2,3,23,24}: return 'white'
        if 4 <= position <= 7: return 'orange'
    return 'normal'

def _table_detail_for_data(data):
    """Keep the Grandscores identity below 24-row tables using the same row spacing."""
    rows=data.get('rows') or []
    if not data.get('endLogo',True):
        return TABLE_DETAIL_NO_LOGO
    if len(rows) != 24:
        return TABLE_DETAIL
    # A 24-row table has four extra rows compared with the 20-row Premier League.
    # Move the identity down by four standard row pitches (4 x 70px).
    shifted=TABLE_DETAIL_NO_LOGO.copy()
    shifted.alpha_composite(TABLE_LOGO_CROP,(500,1200 + 4*70))
    return shifted

def make_table_frame(data, reveal_index=None, progress=1.0, scene='top', transparent=False):
    bg=Image.new('RGBA',(W,H),(0,0,0,0)) if transparent else TABLE_BG.copy()
    label_underlay = bg.crop((160,462,279,559)) if scene=='bottom' else None
    bg.alpha_composite(_table_detail_for_data(data),(0,0))
    if scene=='bottom': _draw_table_label(bg,'Bottom',label_underlay)
    d=ImageDraw.Draw(bg)
    title=f"{data['name']} Table"; tf=font(64); bb=tf.getbbox(title); tx=84-bb[0]; ty=335-bb[1]
    d.text((tx+2,ty+2),title,font=tf,fill=(42,52,76,220)); d.text((tx,ty),title,font=tf,fill=(246,244,104,255))
    selected=_table_selected_rows(data,scene)
    # Keep the exact Premier League row pitch for every division, including 24-row tables.
    pitch=70
    top_y=541
    division_key=int(data.get('league_id') or 0) if str(data.get('league_id','')).isdigit() else 0
    white_positions={8:{18,19,20}}.get(division_key,set())
    if not division_key and data.get('name','').strip().lower()=='premier league': white_positions={18,19,20}
    for idx,r in enumerate(selected):
        y=top_y+idx*pitch
        absolute_position=int(r.get('position') or 0)
        style=_table_row_style(data, absolute_position)
        crop=TABLE_WHITE_CROP if style=='white' else (TABLE_ORANGE_CROP if style=='orange' else TABLE_ROW_CROP)
        bg.alpha_composite(crop,(65,y))
    for idx,r in enumerate(selected):
        y=top_y+idx*pitch
        rev=1.0 if reveal_index is None else (1.0 if idx<reveal_index else (0.0 if idx>reveal_index else max(0,min(1,progress))))
        if rev<=0: continue
        white=_table_row_style(data, int(r.get('position') or 0))=='white'
        _draw_table_row_text(bg,r,y,rev,white=white)
    return bg if transparent else bg.convert('RGB')

def table_voice_script(data):
    custom=str(data.get('voiceoverText') or '').strip()
    if custom:
        marker=re.search(r"And here's the bottom half(?: of the table)?\.??", custom, flags=re.I)
        if marker:
            top_text=custom[:marker.start()].strip()
            bottom_text=custom[marker.start():].strip()
            return [x.strip() for x in re.split(r'(?<=[.!?])\s+', top_text) if x.strip()] + [x.strip() for x in re.split(r'(?<=[.!?])\s+', bottom_text) if x.strip()]
        return [x.strip() for x in re.split(r'(?<=[.!?])\s+', custom) if x.strip()]
    rows=data.get('rows') or []
    half=(len(rows)+1)//2
    top=rows[:half]; bottom=rows[half:]
    mention=bool(data.get('mentionPoints',True))
    if not rows:return [f"The {data.get('name','league')} table."]
    max_played=max([int(r.get('played') or 0) for r in rows] or [0])
    def games_note(r):
        p=int(r.get('played') or 0)
        diff=max_played-p
        if not diff:return ''
        return ' They have a game in hand.' if diff==1 else f' They have {diff} games in hand.'

    def pts(row):
        try:return int(row.get('points',0))
        except Exception:return 0

    lines=[f"The top half of the {data['name']} table."]
    if top:
        r=top[0]; s=f"{vo_team_name(r['name'])} lead the way"
        s+=f" with {r['points']} points from {r['played']} games" if mention else ''
        s+=games_note(r)
        lines.append(s+'.')

        # Vary the commentary naturally. Close title races get a points-gap line;
        # otherwise we occasionally reference the leader's advantage over second.
        if mention and len(top)>1:
            gap=max(0,pts(top[0])-pts(top[1]))
            if gap <= 5:
                lines.append(f"Just {gap} point{'s' if gap != 1 else ''} separate {vo_team_name(top[0]['name'])} and {vo_team_name(top[1]['name'])} at the top." if gap else f"{vo_team_name(top[0]['name'])} and {vo_team_name(top[1]['name'])} are level on points at the top.")
            elif len(rows) % 3 == 0:
                lines.append(f"{vo_team_name(top[0]['name'])} have a {gap}-point lead over {vo_team_name(top[1]['name'])} at the top.")

    for r in top[1:min(3,len(top))]:
        s=f"{vo_team_name(r['name'])} are {ordinal_word(r['position'])}"; s+=f", with {r['points']} points" if mention else ''; lines.append(s+'.')

    if bottom:
        lines.append("And here's the bottom half of the table.")
        r=bottom[-1]; s=f"{vo_team_name(r['name'])} are {ordinal_word(r['position'])} and sit at the bottom"; s+=f", on {r['points']} points" if mention else ''; s+=games_note(r); lines.append(s+'.')
        if len(bottom)>1:
            r=bottom[-2]; s=f"{vo_team_name(r['name'])} are {ordinal_word(r['position'])}"; s+=f", with {r['points']} points" if mention else ''; lines.append(s+'.')
        if len(bottom)>2:
            r=bottom[-3]; s=f"{vo_team_name(r['name'])} are {ordinal_word(r['position'])}"; s+=f", with {r['points']} points" if mention else ''; lines.append(s+'.')
    return lines


def ordinal_word(n):
    n=int(n); return {1:'first',2:'second',3:'third',4:'fourth',5:'fifth',6:'sixth',7:'seventh',8:'eighth',9:'ninth',10:'tenth',11:'eleventh',12:'twelfth',13:'thirteenth',14:'fourteenth',15:'fifteenth',16:'sixteenth',17:'seventeenth',18:'eighteenth',19:'nineteenth',20:'twentieth',21:'twenty-first',22:'twenty-second',23:'twenty-third',24:'twenty-fourth'}.get(n,f"number {n}")

def _write_concat_file(path, clips):
    path.write_text('\n'.join(f"file '{c.as_posix().replace(chr(39),chr(39)+chr(92)+chr(39)+chr(39))}'" for c in clips), encoding='utf-8')


def make_table_audio(data,td,job_id):
    """Generate independent top/bottom VO tracks so the bottom-half narration can never be lost."""
    if not data.get('includeVoiceover',True):
        return None, None, 0.0, 0.0

    rows=data.get('rows') or []
    half=(len(rows)+1)//2
    top=rows[:half]
    bottom=rows[half:]

    # Build the default scripts from the table data first.  The UI's editable
    # script can override the top and bottom wording, but the bottom section
    # is always preserved as a separate VO block.
    generated=table_voice_script({**data, 'voiceoverText':''}) if rows else [f"The {data.get('name','league')} table."]
    marker_re=re.compile(r"And here's the bottom half(?: of the table)?\.?", re.I)
    custom=str(data.get('voiceoverText') or '').strip()

    if custom and marker_re.search(custom):
        marker=marker_re.search(custom)
        top_text=custom[:marker.start()].strip()
        bottom_text=custom[marker.end():].strip()
        top_lines=[x.strip() for x in re.split(r'(?<=[.!?])\s+', top_text) if x.strip()]
        # Keep the transition sentence as part of the bottom VO, so the listener
        # is explicitly told that the bottom half is being shown.
        bottom_lines=["And here's the bottom half of the table."]
        bottom_lines += [x.strip() for x in re.split(r'(?<=[.!?])\s+', bottom_text) if x.strip()]
    elif custom:
        # If someone edits the script and removes the section marker, don't let
        # that accidentally remove the bottom-half narration from the render.
        top_lines=[x.strip() for x in re.split(r'(?<=[.!?])\s+', custom) if x.strip()]
        generated_marker=next((i for i,x in enumerate(generated) if x.lower().startswith("and here's the bottom half")), None)
        bottom_lines=generated[generated_marker:] if generated_marker is not None else []
    else:
        marker_idx=next((i for i,x in enumerate(generated) if x.lower().startswith("and here's the bottom half")), None)
        if marker_idx is None:
            top_lines=generated
            bottom_lines=[]
        else:
            top_lines=generated[:marker_idx]
            bottom_lines=generated[marker_idx:]

    # A populated table should always have a bottom VO section. This fallback
    # protects against malformed/custom text while retaining the editable top VO.
    if bottom and not bottom_lines:
        mention=bool(data.get('mentionPoints',True))
        def games_note_local(r):
            max_played=max([int(x.get('played') or 0) for x in rows] or [0])
            p=int(r.get('played') or 0); diff=max_played-p
            if not diff:return ''
            return ' They have a game in hand.' if diff==1 else f' They have {diff} games in hand.'
        bottom_lines=["And here's the bottom half of the table."]
        r=bottom[-1]; line=f"{r['name']} are {ordinal_word(r['position'])} and sit at the bottom"
        if mention: line+=f", on {r['points']} points"
        bottom_lines.append(line+games_note_local(r)+'.')
        if len(bottom)>1:
            r=bottom[-2]; line=f"{r['name']} are {ordinal_word(r['position'])}"
            if mention: line+=f", with {r['points']} points"
            bottom_lines.append(line+'.')
        if len(bottom)>2:
            r=bottom[-3]; line=f"{r['name']} are {ordinal_word(r['position'])}"
            if mention: line+=f", with {r['points']} points"
            bottom_lines.append(line+'.')

    all_lines=top_lines+bottom_lines
    clips=[]
    for i,line in enumerate(all_lines):
        clip=td/f'table_voice_{i:02d}.mp3'
        elevenlabs_tts(vo_text(line),clip)
        clips.append(clip)
        with LOCK:
            JOBS[job_id]['progress']=5+int((i+1)/max(len(all_lines),1)*10)
            JOBS[job_id]['message']=f'Generating table voiceover: clip {i+1} of {len(all_lines)}…'

    top_count=len(top_lines)
    top_clips=clips[:top_count]
    bottom_clips=clips[top_count:]
    top_voice=td/'table_voice_top.mp3'
    _write_concat_file(td/'table_top_clips.txt',top_clips)
    run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(td/'table_top_clips.txt'),'-c','copy',str(top_voice)])
    top_dur=audio_duration(top_voice)

    bottom_voice=None; bottom_dur=0.0
    if bottom_clips:
        bottom_voice=td/'table_voice_bottom.mp3'
        _write_concat_file(td/'table_bottom_clips.txt',bottom_clips)
        run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(td/'table_bottom_clips.txt'),'-c','copy',str(bottom_voice)])
        bottom_dur=audio_duration(bottom_voice)
    return top_voice, bottom_voice, top_dur, bottom_dur


def render_table_video(data,top_duration,bottom_duration,out_path,job_id):
    rows=data.get('rows') or []; half=(len(rows)+1)//2
    top_n=len(rows[:half]); bottom_n=len(rows[half:])
    top_reveal=4*top_n+2*max(0,top_n-1)
    bottom_reveal=4*bottom_n+2*max(0,bottom_n-1)
    transition_frames=12
    top_min_frames=top_reveal+25
    top_vo_frames=int(round(top_duration*FPS))
    transition_start=max(top_min_frames,top_vo_frames)
    hold_top=max(0,transition_start-top_reveal)
    bottom_hold=25+int(round(bottom_duration*FPS))
    total=transition_start+transition_frames+bottom_reveal+bottom_hold
    animated=animated_background_enabled(data)
    if animated:
        with tempfile.TemporaryDirectory(prefix='grandscores_table_fg_') as fd:
            frame_dir=Path(fd); frame_no=1
            def save(frame):
                nonlocal frame_no
                _write_animated_png_frame(frame,frame_dir/f'{frame_no:06d}.png'); frame_no+=1
            for i in range(top_n):
                for fidx in range(4): save(make_table_frame(data,i,(fidx+1)/4,'top',transparent=True))
                if i<top_n-1:
                    save(make_table_frame(data,i,1.0,'top',transparent=True)); save(make_table_frame(data,i,1.0,'top',transparent=True))
            top_final=make_table_frame(data,top_n,1.0,'top',transparent=True)
            for _ in range(hold_top): save(top_final)
            del top_final
            top_static=make_table_frame(data,top_n,1.0,'top',transparent=True)
            bottom_empty=make_table_frame(data,0,0.0,'bottom',transparent=True)
            for fidx in range(transition_frames):
                rev=(fidx+1)/transition_frames; frame=top_static.copy(); cut=int(W*rev); frame.paste(bottom_empty.crop((0,0,cut,H)),(0,0)); save(frame); del frame
            del top_static,bottom_empty
            for i in range(bottom_n):
                for fidx in range(4): save(make_table_frame(data,i,(fidx+1)/4,'bottom',transparent=True))
                if i<bottom_n-1:
                    save(make_table_frame(data,i,1.0,'bottom',transparent=True)); save(make_table_frame(data,i,1.0,'bottom',transparent=True))
            bottom_final=make_table_frame(data,bottom_n,1.0,'bottom',transparent=True)
            for _ in range(bottom_hold): save(bottom_final)
            del bottom_final
            _encode_animated_foreground(frame_dir,out_path,total)
        return

    cmd=['ffmpeg','-y','-loglevel','error','-f','image2pipe','-vcodec','ppm','-framerate',str(FPS),'-i','-','-c:v','libx264','-preset','ultrafast','-threads','1','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(out_path)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        def send(frame): frame.save(proc.stdin,format='PPM')
        for i in range(top_n):
            for fidx in range(4): send(make_table_frame(data,i,(fidx+1)/4,'top'))
            if i<top_n-1: send(make_table_frame(data,i,1.0,'top')); send(make_table_frame(data,i,1.0,'top'))
        top_final=make_table_frame(data,top_n,1.0,'top')
        for _ in range(hold_top): send(top_final)
        del top_final
        top_static=make_table_frame(data,top_n,1.0,'top'); bottom_empty=make_table_frame(data,0,0.0,'bottom')
        for fidx in range(transition_frames):
            rev=(fidx+1)/transition_frames; frame=top_static.copy(); cut=int(W*rev); frame.paste(bottom_empty.crop((0,0,cut,H)),(0,0)); send(frame); del frame
        del top_static,bottom_empty
        for i in range(bottom_n):
            for fidx in range(4): send(make_table_frame(data,i,(fidx+1)/4,'bottom'))
            if i<bottom_n-1: send(make_table_frame(data,i,1.0,'bottom')); send(make_table_frame(data,i,1.0,'bottom'))
        bottom_final=make_table_frame(data,bottom_n,1.0,'bottom')
        for _ in range(bottom_hold): send(bottom_final)
        del bottom_final
        proc.stdin.close(); rc=proc.wait()
        if rc!=0: raise RuntimeError('Table video encoding failed')
    except Exception:
        try: proc.stdin.close()
        except: pass
        proc.kill(); proc.wait(); raise

def _competition_code(name):
    n=str(name or '').strip().lower()
    return {'premier league':'PL','championship':'CH','league one':'L1','league two':'L2'}.get(n, ''.join(w[:1] for w in n.split()[:3]).upper() or 'GSS')

PRINTER_Y=410
PRINTER_RESULT_GAP=30
PRINTER_LINE_GAP=48
PRINTER_MAX_PER_PAGE=5
PRINTER_BOTTOM_LIMIT=1480

def _printer_scorer_lines(result):
    grouped=[]
    for s in (result.get('scorers') or []):
        if not isinstance(s,dict): continue
        name=str(s.get('name') or '').strip()
        if name and ' ' in name:
            name=name.split()[-1]
        side=str(s.get('team') or '').lower()
        if not name or side not in ('home','away'): continue
        grouped.append((side,name))
    out=[]
    for side in ('home','away'):
        counts={}; order=[]
        for sc_side,name in grouped:
            if sc_side!=side: continue
            key=name.lower()
            if key not in counts: order.append(name); counts[key]=0
            counts[key]+=1
        out.extend((side,name,counts[name.lower()]) for name in order)
    return out

def _printer_layout(page):
    """Return result Y positions based on the previous result's last scorer."""
    ys=[]
    y=PRINTER_Y
    for idx,(_,r) in enumerate(page):
        ys.append(y)
        scorers=_printer_scorer_lines(r)
        max_sc=max(len([x for x in scorers if x[0]=='home']),len([x for x in scorers if x[0]=='away']))
        last_bottom=y+52
        if max_sc:
            last_bottom=y+65+(max_sc-1)*PRINTER_LINE_GAP+42
        y=last_bottom+PRINTER_RESULT_GAP
    return ys

def _printer_paginate(data):
    """Pack up to five results per page, using content height rather than fixed rows."""
    pages=[]
    for sec in data.get('leagues') or []:
        comp=str(sec.get('competition') or 'Competition')
        completed=[r for r in (sec.get('results') or []) if r.get('homeScore') is not None and r.get('awayScore') is not None]
        current=[]
        for r in completed:
            trial=current+[(comp,r)]
            ys=_printer_layout(trial)
            scorers=_printer_scorer_lines(r)
            max_sc=max(len([x for x in scorers if x[0]=='home']),len([x for x in scorers if x[0]=='away']))
            bottom=ys[-1]+(65+(max_sc-1)*PRINTER_LINE_GAP+42 if max_sc else 52)
            if current and (len(trial)>PRINTER_MAX_PER_PAGE or bottom>PRINTER_BOTTOM_LIMIT):
                pages.append(current); current=[(comp,r)]
            else:
                current=trial
        if current: pages.append(current)
    return pages or [[]]

def _printer_draw_cursor(base,x,y,font_obj,visible=True):
    if not visible:return
    d=ImageDraw.Draw(base); d.text((int(x),int(y)), '*', font=font_obj, fill=(246,244,104,255))

def _printer_draw_line(base,text,x,y,visible_chars,font_obj,fill,shadow=True,cursor=False,cursor_on=True):
    text=str(text or '')
    shown=text[:max(0,int(visible_chars))]
    d=ImageDraw.Draw(base)
    if shown:
        bb=font_obj.getbbox(shown); px=int(x-bb[0]); py=int(y-bb[1])
        if shadow:d.text((px+2,py+2),shown,font=font_obj,fill=(42,52,76,220))
        d.text((px,py),shown,font=font_obj,fill=fill)
        end=d.textlength(shown,font=font_obj)
    else:end=0
    if cursor and cursor_on:_printer_draw_cursor(base,x+end+7,y,font_obj,True)

def _printer_result_line(base,result,visible_chars,font_obj,cursor_on=True,y=PRINTER_Y):
    home=str(result.get('home') or ''); score=score_text(result); away=str(result.get('away') or '')
    n=max(0,int(visible_chars)); hc=min(n,len(home)); sc=min(max(n-len(home),0),len(score)); ac=min(max(n-len(home)-len(score),0),len(away))
    d=ImageDraw.Draw(base)
    def draw_segment(text,count,x):
        if count<=0:return 0
        shown=text[:count]; bb=font_obj.getbbox(shown); px=int(x-bb[0]); py=int(y-bb[1]); d.text((px+2,py+2),shown,font=font_obj,fill=(42,52,76,220)); d.text((px,py),shown,font=font_obj,fill=(242,242,234,255)); return d.textlength(shown,font=font_obj)
    draw_segment(home,hc,92)
    score_w=font_obj.getbbox(score)[2]-font_obj.getbbox(score)[0]; score_x=(473+607)/2-score_w/2
    draw_segment(score,sc,score_x); draw_segment(away,ac,638)
    if cursor_on:
        if hc<len(home): cx=92+draw_segment(home,hc,92)+7
        elif sc<len(score): cx=score_x+draw_segment(score,sc,score_x)+7
        else: cx=638+draw_segment(away,ac,638)+7
        _printer_draw_cursor(base,cx,y,font_obj,True)

def make_printer_frame(data, page_index=0, line_states=None, cursor_on=True, transparent=False):
    base=Image.new('RGBA',(W,H),(0,0,0,0)) if transparent else STATIC.copy()
    pages=_printer_paginate(data)
    page=pages[min(page_index,len(pages)-1)]
    title=(page[0][0] if page else 'Full Time Results').upper()
    tf=ImageFont.truetype(str(PRINTER_TITLE_FONT_PATH),64); d=ImageDraw.Draw(base); bb=tf.getbbox(title); title_y=265; d.text((84-bb[0]+2,title_y-bb[1]+2),title,font=tf,fill=(42,52,76,220)); d.text((84-bb[0],title_y-bb[1]),title,font=tf,fill=(246,244,104,255))
    team_f=font(RESULT_FONT_SIZE); scorer_f=font(RESULT_FONT_SIZE)
    ys=_printer_layout(page)
    active=line_states is not None
    for idx,(_,r) in enumerate(page):
        y=ys[idx]
        result_text=f"{r.get('home','')}  {score_text(r)}  {r.get('away','')}"
        result_len=len(result_text)
        st=(line_states or {}).get((idx,'result'), result_len if not active else 0)
        result_complete=st>=result_len
        # A cursor is drawn ONLY when this exact result line is the active
        # typing target. This prevents completed results from retaining a
        # blinking cursor while a later scorer/result is being printed.
        result_cursor_active=(line_states or {}).get('_active')==(idx,'result')
        _printer_result_line(base,r,st,team_f,cursor_on=cursor_on and result_cursor_active and st<result_len,y=y)
        scorers=_printer_scorer_lines(r)
        home_sc=[x for x in scorers if x[0]=='home']; away_sc=[x for x in scorers if x[0]=='away']
        max_sc=max(len(home_sc),len(away_sc))
        for si in range(max_sc):
            sy=y+65+si*PRINTER_LINE_GAP
            for side,arr,x in (('home',home_sc,92),('away',away_sc,638)):
                if si>=len(arr): continue
                _,name,count=arr[si]; text=name+(f" {count}" if count>1 else '')
                st=(line_states or {}).get((idx,side,si), len(text) if not active else 0)
                # Only the currently typing line gets a cursor. Completed scorer lines stay clean.
                current_key=(idx,side,si)
                cursor_here=cursor_on and (line_states or {}).get('_active')==current_key and st<len(text)
                _printer_draw_line(base,text,x,sy,st,scorer_f,(246,244,104,255),cursor=cursor_here,cursor_on=cursor_here)
    last_content_bottom=0
    if page:
        for idx,(_,r) in enumerate(page):
            y=ys[idx]; scorers=_printer_scorer_lines(r)
            max_sc=max(len([x for x in scorers if x[0]=='home']),len([x for x in scorers if x[0]=='away']))
            last_content_bottom=max(last_content_bottom,y+(65+(max_sc-1)*PRINTER_LINE_GAP+42 if max_sc else 52))
    logo_y=max(1200,last_content_bottom+45)
    if logo_y+TABLE_LOGO_CROP.height>H: logo_y=max(0,H-TABLE_LOGO_CROP.height)
    base.alpha_composite(TABLE_LOGO_CROP,(500,int(logo_y)))
    return base if transparent else base.convert('RGB')

def _printer_voice_line(r):
    hs='nill' if int(r.get('homeScore') or 0)==0 else {1:'one',2:'two',3:'three',4:'four',5:'five',6:'six',7:'seven',8:'eight',9:'nine',10:'ten'}.get(int(r.get('homeScore')),str(r.get('homeScore')))
    aws='nill' if int(r.get('awayScore') or 0)==0 else {1:'one',2:'two',3:'three',4:'four',5:'five',6:'six',7:'seven',8:'eight',9:'nine',10:'ten'}.get(int(r.get('awayScore')),str(r.get('awayScore')))
    return vo_text(f"{r.get('home','')}, <break time=\"0.18s\" /> {hs}. <break time=\"0.24s\" /> {r.get('away','')}, <break time=\"0.18s\" /> {aws}.")

def _make_printer_sfx(path, events, duration):
    """Build the Vidi Printer SFX track from the bundled printer_typing.wav asset.
    The asset is a short dot-matrix character sound; this function places a copy
    at each character reveal time so the final render has a true timed SFX track.
    """
    import wave, struct
    sr=44100
    with wave.open(str(PRINTER_SFX_PATH),'rb') as src:
        channels=src.getnchannels(); width=src.getsampwidth(); src_sr=src.getframerate()
        raw=src.readframes(src.getnframes())
    if channels != 1 or width != 2 or src_sr != sr:
        raise RuntimeError('printer_typing.wav must be 44.1kHz mono 16-bit PCM')
    sample_count=len(raw)//2
    sample=struct.unpack('<%dh' % sample_count, raw)
    total=max(1,int(duration*sr))
    mix=[0]*total
    for t in events:
        start=int(max(0,t)*sr)
        if start>=total: continue
        end=min(total,start+sample_count)
        for j,v in enumerate(sample[:end-start]):
            mix[start+j] += v
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        out=bytearray()
        for v in mix:
            # Leave headroom here; final FFmpeg mix applies the 0.18 SFX weight.
            v=max(-32767,min(32767,v))
            out.extend(struct.pack('<h',v))
        w.writeframes(out)

def render_printer_video(data,out_path,job_id):
    pages=_printer_paginate(data); animated=animated_background_enabled(data)
    with tempfile.TemporaryDirectory(prefix='grandscores_printer_') as td0:
        td=Path(td0); frame_dir=td/'frames'; frame_dir.mkdir();
        # Generate the VO first so the visual timeline can hold each result until
        # its spoken result has finished. The actual wording follows the Results
        # Video pronunciation system, including all team-name aliases.
        voice_clips=[]; voice_durations=[]
        total_results=sum(len(p) for p in pages)
        done_results=0
        for pi,page in enumerate(pages):
            for idx,(_,r) in enumerate(page):
                clip=td/f'voice_{done_results:03d}.mp3'
                elevenlabs_tts(_printer_voice_line(r),clip)
                voice_clips.append(clip); voice_durations.append(audio_duration(clip)); done_results+=1
                with LOCK:
                    JOBS[job_id]['progress']=3+int(done_results/max(total_results,1)*12)
                    JOBS[job_id]['message']=f'Generating voiceover: result {done_results} of {total_results}…'

        frame_no=1; total=0; timeline=0.0; sfx_events=[]; voice_delays=[]; voice_index=0
        rng=random.Random()
        for pi,page in enumerate(pages):
            states={};
            for idx,(_,r) in enumerate(page):
                # Add a random live-feed pause between completed results. The
                # first result starts immediately; later results wait 1–5 sec.
                if idx>0 or pi>0:
                    pause=rng.uniform(1.0,5.0)
                    pause_frames=max(25,int(round(pause*FPS)))
                    # During the live-feed pause, park a single blinking cursor
                    # at the start of the NEXT result line. It must not appear
                    # on any previously completed result/scorer line.
                    states[(idx,'result')]=0
                    states['_active']=(idx,'result')
                    for pf in range(pause_frames):
                        im=make_printer_frame(data,pi,states,cursor_on=((pf//12)%2==0),transparent=animated)
                        _write_animated_png_frame(im,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; total+=1
                    states['_active']=None
                    timeline += pause_frames/FPS

                result=f"{r.get('home','')}  {score_text(r)}  {r.get('away','')}"
                scorer_lines=_printer_scorer_lines(r)
                states[(idx,'result')]=0
                max_sc=max(len([x for x in scorer_lines if x[0]=='home']),len([x for x in scorer_lines if x[0]=='away']))
                for si in range(max_sc):
                    for side in ('home','away'):
                        arr=[x for x in scorer_lines if x[0]==side]
                        if si<len(arr): states[(idx,side,si)]=0

                voice_delays.append(timeline)
                voice_duration=voice_durations[voice_index]; voice_index+=1

                # Type the result and then its goalscorers one character at a
                # time. The cursor is only attached to the line currently being
                # typed, so completed lines cannot flicker with stray '*'
                # characters.
                for n in range(1,len(result)+1):
                    states[(idx,'result')]=n; states['_active']=(idx,'result')
                    im=make_printer_frame(data,pi,states,cursor_on=(((n-1)//2)%2==0),transparent=animated)
                    _write_animated_png_frame(im,frame_dir/f'{frame_no:06d}.png'); sfx_events.append(timeline); frame_no+=1; total+=1; timeline+=1/FPS
                for si in range(max_sc):
                    for side in ('home','away'):
                        arr=[x for x in scorer_lines if x[0]==side]
                        if si>=len(arr): continue
                        text=arr[si][1]+(f" {arr[si][2]}" if arr[si][2]>1 else '')
                        for n in range(1,len(text)+1):
                            states[(idx,side,si)]=n; states['_active']=(idx,side,si)
                            im=make_printer_frame(data,pi,states,cursor_on=(((n-1)//2)%2==0),transparent=animated)
                            _write_animated_png_frame(im,frame_dir/f'{frame_no:06d}.png'); sfx_events.append(timeline); frame_no+=1; total+=1; timeline+=1/FPS

                # Keep the fully revealed result on screen until its VO has
                # finished, then leave a small clean hold before the next pause.
                states['_active']=None
                # Use the actual frame timeline instead of trying to infer the
                # scorer duration again; the voice start was recorded above.
                desired_end=voice_delays[-1]+max(voice_duration,0.0)+0.25
                hold=max(0.25,desired_end-timeline)
                hold_frames=max(1,int(round(hold*FPS)))
                for h in range(hold_frames):
                    im=make_printer_frame(data,pi,states,cursor_on=False,transparent=animated)
                    _write_animated_png_frame(im,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; total+=1; timeline+=1/FPS

            # Small clean end-of-page hold.
            for _ in range(25):
                im=make_printer_frame(data,pi,states,cursor_on=False,transparent=animated)
                _write_animated_png_frame(im,frame_dir/f'{frame_no:06d}.png'); frame_no+=1; total+=1; timeline+=1/FPS
            with LOCK:
                JOBS[job_id]['progress']=15+int((pi+1)/len(pages)*55)
                JOBS[job_id]['message']=f'Rendering Vidi Printer page {pi+1} of {len(pages)}…'

        sfx=td/'printer_typing.wav'; _make_printer_sfx(sfx,sfx_events,timeline+0.2)
        cmd=['ffmpeg','-y','-loglevel','error']
        for p in voice_clips: cmd += ['-i',str(p)]
        cmd += ['-i',str(sfx)]
        parts=[]
        for i,delay in enumerate(voice_delays):
            parts.append(f'[{i}:a]adelay={int(delay*1000)}:all=1[v{i}]')
        labels=''.join(f'[v{i}]' for i in range(len(voice_clips)))
        parts.append(f'[{len(voice_clips)}:a]volume=0.18[sfx];{labels}[sfx]amix=inputs={len(voice_clips)+1}:duration=longest:normalize=0[a]')
        cmd += ['-filter_complex',';'.join(parts),'-map','[a]','-c:a','aac','-b:a','128k',str(td/'printer_audio.m4a')]
        run_ffmpeg(cmd); audio=td/'printer_audio.m4a'

        if animated:
            silent=td/'printer_video.mp4'; _encode_animated_foreground(frame_dir,silent,total)
        else:
            silent=td/'printer_video.mp4'
            run_ffmpeg(['ffmpeg','-y','-loglevel','error','-framerate',str(FPS),'-i',str(frame_dir/'%06d.png'),'-c:v','libx264','-preset','ultrafast','-threads','1','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(silent)])
        run_ffmpeg(['ffmpeg','-y','-loglevel','error','-i',str(silent),'-i',str(audio),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','128k','-shortest','-movflags','+faststart',str(out_path)])

def _render_filename(data, kind):

    date_code=datetime.now(UK_TZ).strftime('%d%m%Y')
    if kind=='results':
        leagues=data.get('leagues') or []
        codes='-'.join(_competition_code(x.get('competition')) for x in leagues) or 'GSS'
        return f"{date_code}_{codes}-Results_GSS.mp4"
    if kind=='printer':
        leagues=data.get('leagues') or []
        codes='-'.join(_competition_code(x.get('competition')) for x in leagues) or 'GSS'
        return f"{date_code}_{codes}-Vidi_Printer_GSS.mp4"
    return f"{date_code}_{_competition_code(data.get('name'))}-League_GSS.mp4"


def render_table_job(job_id,data):
    with RENDER_LOCK:
        try:
            with LOCK:JOBS[job_id].update(status='rendering',progress=2,message='Preparing league table…')
            with tempfile.TemporaryDirectory(prefix='grandscores_table_') as td0:
                td=Path(td0)
                top_voice,bottom_voice,top_dur,bottom_dur=make_table_audio(data,td,job_id)
                silent=td/'table_silent.mp4'
                render_table_video(data,top_dur,bottom_dur,silent,job_id)
                out=OUT/_render_filename(data, 'table'); intro=BASE/'assets'/'grandscores_intro_music.wav'
                # Build one voice track with the bottom VO starting exactly when
                # the visual wipe begins. This prevents "bottom half" narration
                # from being spoken while the top half is still on screen.
                voice=None
                if top_voice:
                    rows=data.get('rows') or []; half=(len(rows)+1)//2
                    top_reveal=4*len(rows[:half])+2*max(0,len(rows[:half])-1)
                    transition_start=max(top_reveal+25,int(round(top_dur*FPS)))
                    transition_sec=transition_start/FPS
                    transition_duration=12/FPS
                    if bottom_voice:
                        # Start bottom-half narration after the 12-frame wipe has
                        # completed, so the spoken section matches the visual.
                        gap=max(0.0,(transition_sec+transition_duration)-top_dur)
                        gap_file=td/'table_voice_gap.wav'
                        run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','anullsrc=r=44100:cl=stereo','-t',f'{gap:.3f}','-c:a','libmp3lame','-b:a','160k',str(gap_file.with_suffix('.mp3'))])
                        gap_file=gap_file.with_suffix('.mp3')
                        cat=td/'table_voice_final.txt'; _write_concat_file(cat,[top_voice,gap_file,bottom_voice])
                        voice=td/'table_voice_final.mp3'
                        run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(cat),'-c:a','libmp3lame','-b:a','160k',str(voice)])
                    else:
                        voice=top_voice
                inputs=['ffmpeg','-y','-loglevel','error','-i',str(silent)]
                if voice: inputs+=['-i',str(voice)]
                if data.get('includeIntroMusic',True) and intro.exists(): inputs+=['-i',str(intro)]
                if voice and data.get('includeIntroMusic',True) and intro.exists():
                    filt='[2:a]volume=0.20[intro];[1:a][intro]amix=inputs=2:duration=first:dropout_transition=0[a]'
                    inputs+=['-filter_complex',filt,'-map','0:v','-map','[a]']
                elif voice: inputs+=['-map','0:v','-map','1:a']
                else:
                    inputs+=['-f','lavfi','-i','anullsrc=r=44100:cl=stereo','-map','0:v','-map','1:a']
                    inputs+=['-shortest']
                # Do not use -shortest: the video length is deliberately built
                # around the exact VO durations above.
                inputs+=['-c:v','copy','-c:a','aac','-b:a','160k','-movflags','+faststart',str(out)]
                run_ffmpeg(inputs)
                with LOCK:JOBS[job_id].update(status='done',progress=100,message='League table video ready.',path=str(out))
        except Exception as e:
            print('TABLE RENDER ERROR',repr(e),flush=True)
            with LOCK:JOBS[job_id].update(status='error',progress=100,message=f'Table render failed: {e}')

def render_job(job_id,data):
    with RENDER_LOCK:
        try:
            with LOCK: JOBS[job_id].update(status='rendering',progress=2,message='Preparing voiceovers…')
            with tempfile.TemporaryDirectory(prefix='grandscores_') as td0:
                td=Path(td0); audio,durations,data=make_audio_multi(data,td,job_id); silent=td/'silent.mp4'
                # Resolve the Results intro before rendering the graphics because its duration
                # determines how long the final frame must be held.
                intro=BASE/'assets'/'ElevenLabs_2026-09-19T07_02_06_Like Graham Alexander_gen_s50_m2.mp3'
                use_intro=bool(data.get('includeIntroMusic', True))
                intro_duration=audio_duration(intro) if use_intro and intro.exists() else 0.0
                with LOCK: JOBS[job_id].update(progress=10,message='Rendering two-league graphics…')
                stream_video_multi(data,durations,silent,job_id,extra_tail_seconds=intro_duration)
                with LOCK: JOBS[job_id].update(progress=94,message='Adding voiceover and intro music…')
                out=OUT/_render_filename(data, 'results')
                # The intro plays before the VO, so the graphics need an equal amount of extra
                # hold time at the end. Otherwise -shortest would cut the final VO short.
                if use_intro and intro.exists():
                    # Play the intro once at the very beginning, then start the VO.
                    # The two tracks are concatenated, never mixed/overlapped.
                    intro_audio=td/'results_intro.mp3'
                    run_ffmpeg(['ffmpeg','-y','-loglevel','error','-i',str(intro),'-c:a','libmp3lame','-b:a','160k',str(intro_audio)])
                    concat=td/'results_audio.txt'; _write_concat_file(concat,[intro_audio,audio])
                    final_audio=td/'results_audio_final.m4a'
                    run_ffmpeg(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(concat),'-c:a','aac','-b:a','160k',str(final_audio)])
                    run_ffmpeg(['ffmpeg','-y','-loglevel','error','-i',str(silent),'-i',str(final_audio),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','160k','-shortest','-movflags','+faststart',str(out)])
                else:
                    # Music disabled, or asset unavailable: render voiceover only.
                    run_ffmpeg(['ffmpeg','-y','-loglevel','error','-i',str(silent),'-i',str(audio),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','160k','-shortest','-movflags','+faststart',str(out)])
                with LOCK: JOBS[job_id].update(status='done',progress=100,message='Video ready.',path=str(out))
        except Exception as e:
            print('RENDER ERROR',repr(e),flush=True)
            with LOCK: JOBS[job_id].update(status='error',progress=100,message=f'Render failed: {e}')


TARGET_LEAGUES = {'premier league', 'championship', 'league one', 'league two'}
UK_TZ = ZoneInfo('Europe/London')

def _participant_location(p):
    return str((p.get('meta') or {}).get('location') or '').lower()

def _score_value(scores, participant_id):
    candidates=[]
    for sc in scores or []:
        if sc.get('participant_id') == participant_id:
            candidates.append((str(sc.get('description') or '').upper(), sc.get('score') or {}))
    preferred=['CURRENT','2ND_HALF','1ST_HALF','ET','PENALTY','FT','AET','AP']
    for pref in preferred:
        for desc,obj in candidates:
            if desc == pref and obj.get('goals') is not None:
                try: return int(obj.get('goals'))
                except Exception: pass
    for _,obj in candidates:
        if obj.get('goals') is not None:
            try: return int(obj.get('goals'))
            except Exception: pass
    return None

def _fixture_kind(state_name):
    s=str(state_name or '').lower()
    if any(x in s for x in ('live','1st','2nd','half','extra','inplay','in play')): return 'live'
    if any(x in s for x in ('finished','full time','ft','after')): return 'finished'
    return 'scheduled'

def _format_fixture_time(value):
    if not value: return ''
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(UK_TZ).strftime('%H:%M')
    except Exception: return str(value)[:16]

def _format_fixture_date(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(UK_TZ).strftime('%Y-%m-%d')
    except Exception: return ''

def _elapsed_minutes(starting_at, state_name):
    s=str(state_name or '').lower()
    if 'half' in s: return 45
    try:
        dt=datetime.fromisoformat(str(starting_at).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        elapsed=int((datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()//60)
        if '1st' in s or 'first' in s: return max(1,min(45,elapsed))
        if any(x in s for x in ('2nd','second','in play','inplay','live')): return max(46,min(90,elapsed))
        if 'extra' in s: return max(91,min(120,elapsed))
        return max(1,min(90,elapsed))
    except Exception: return None

def _fixture_goalscorers(f, home_id, away_id):
    events=f.get('events') or []
    out=[]
    for e in sorted(events,key=lambda x:(x.get('sort_order') is None,x.get('sort_order') or 0)):
        tid=e.get('type_id'); typ=e.get('type') or {}; code=str(typ.get('developer_name') or typ.get('code') or '').upper()
        if tid not in (14,15,16) and code not in ('GOAL','OWNGOAL','PENALTY'): continue
        name=str(e.get('player_name') or '').strip()
        if not name: continue
        pid=e.get('participant_id')
        if code=='OWNGOAL' or tid==15:
            side='away' if pid==home_id else 'home' if pid==away_id else ''
        else:
            side='home' if pid==home_id else 'away' if pid==away_id else ''
        if side: out.append({'name':name,'team':side})
    return out

def _normalise_fixture(f):
    participants=f.get('participants') or []
    home=next((p for p in participants if _participant_location(p)=='home'), None)
    away=next((p for p in participants if _participant_location(p)=='away'), None)
    if home is None and participants: home=participants[0]
    if away is None and len(participants)>1: away=participants[1]
    home=home or {}; away=away or {}
    league=f.get('league') or {}; state=f.get('state') or {}
    state_name=state.get('name') or state.get('developer_name') or f.get('state_name') or ''
    scores=f.get('scores') or []
    return {'id':f.get('id'),'league':league.get('name') or f.get('league_name') or '',
            'home':home.get('name') or '','away':away.get('name') or '',
            'home_score':_score_value(scores,home.get('id')),'away_score':_score_value(scores,away.get('id')),'scorers':_fixture_goalscorers(f,home.get('id'),away.get('id')),
            'state':state_name,'kind':_fixture_kind(state_name),'time':_format_fixture_time(f.get('starting_at')),'date':_format_fixture_date(f.get('starting_at')),'elapsed_minutes':_elapsed_minutes(f.get('starting_at'),state_name) if _fixture_kind(state_name)=='live' else None}

def _mock_live_fixtures(start=None,end=None):
    """Test feed. Today includes a live and an upcoming game; earlier days are all finished."""
    today=_date.fromisoformat(_fixture_date_today())
    start=start or today; end=end or start
    base=[
        ('Premier League','Arsenal','Chelsea',2,1,[('Saka','home'),('Saka','home'),('Palmer','away')]),
        ('Premier League','Liverpool','Everton',3,0,[('Salah','home'),('Gakpo','home'),('Szoboszlai','home')]),
        ('Championship','Leeds United','Middlesbrough',1,1,[('Gnonto','home'),('Latte Lath','away')]),
        ('Championship','Sunderland','Norwich City',0,2,[('Sargent','away'),('Sainz','away')]),
        ('League One','Barnsley','Wigan Athletic',0,0,[]),
        ('League Two','Bradford City','Chesterfield',3,2,[('Cook','home'),('Cook','home'),('Pattison','home'),('Mandeville','away'),('Mandeville','away')]),
        ('Scottish Premiership','Celtic','Hibernian',2,0,[('Kuhn','home'),('Maeda','home')]),
    ]
    out=[]; d=start; n=0
    while d<=end:
        rot=n%len(base)
        for i,(lg,h,a,hs,as_,sc) in enumerate(base[rot:]+base[:rot]):
            kind='finished'; state='Finished'; t='15:00'; mins=None
            if d==today and i==0: kind,state,mins='live','2nd Half',67
            if d==today and i==len(base)-1: kind,state,t,hs,as_,sc='scheduled','Not Started','19:45',None,None,[]
            out.append({'id':f'test-{d.isoformat()}-{i}','league':lg,'home':h,'away':a,'home_score':hs,'away_score':as_,
                        'scorers':[{'name':x,'team':s} for x,s in sc],'state':state,'kind':kind,'time':t,'date':d.isoformat(),'elapsed_minutes':mins})
        d+=timedelta(days=1); n+=1
    return out

def _sportmonks_get(url, token):
    req=urllib.request.Request(url+'&api_token='+urllib.parse.quote(token),headers={'Accept':'application/json'},method='GET')
    with urllib.request.urlopen(req,timeout=20) as resp:
        return json.loads(resp.read().decode('utf-8'))

def _sportmonks_get_all(url, token, max_pages=10):
    """Follow Sportmonks pagination so busy matchdays are not cut off at 50 fixtures."""
    out=[]; page=1
    while page<=max_pages:
        payload=_sportmonks_get(f'{url}&page={page}',token)
        out.extend(payload.get('data') or [])
        if not (payload.get('pagination') or {}).get('has_more'): break
        page+=1
    return out

def _fixture_date_today():
    return datetime.now(UK_TZ).strftime('%Y-%m-%d')

MAX_RANGE_DAYS=7
FIXTURE_INCLUDES='include=scores;participants;league;state;events&filters=eventTypes:14,15,16'

def _parse_day(value):
    try: return _date.fromisoformat(str(value))
    except Exception: return None

def _requested_range():
    """Return (start, end) dates from ?date= or ?start=&end=, defaulting to today."""
    today=_date.fromisoformat(_fixture_date_today())
    if request.args.get('date'):
        d=_parse_day(request.args['date'])
        if not d: raise ValueError('Dates must be written as YYYY-MM-DD.')
        start=end=d
    else:
        start=_parse_day(request.args.get('start')) or today
        end=_parse_day(request.args.get('end')) or start
    if end<start: start,end=end,start
    if start>today: raise ValueError('Choose today or an earlier date.')
    if end>today: end=today
    if (end-start).days+1>MAX_RANGE_DAYS: raise ValueError(f'Choose a range of {MAX_RANGE_DAYS} days or fewer.')
    return start,end,today

def _range_label(start,end,today):
    if start==end==today: return 'Today'
    if start==end: return start.strftime('%A %-d %B')
    return f"{start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"

@app.get('/api/live-scores')
@app.get('/api/fixtures')
def api_live_scores():
    try: start,end,today=_requested_range()
    except ValueError as e: return jsonify(error=str(e)),400
    meta=dict(start=start.isoformat(),end=end.isoformat(),label=_range_label(start,end,today),includes_today=end==today)
    if request.args.get('mode') == 'test':
        return jsonify(fixtures=_mock_live_fixtures(start,end),updated_at=datetime.now(UK_TZ).strftime('%d %b %Y %H:%M'),source='mock',**meta)
    token=os.getenv('SPORTMONKS_API_TOKEN')
    if not token: return jsonify(error='SPORTMONKS_API_TOKEN is not set in the server environment. Add it under Variables in Railway.'),500
    try:
        base='https://api.sportmonks.com/v3/football'
        if start==end: day_url=f'{base}/fixtures/date/{start.isoformat()}?{FIXTURE_INCLUDES}&per_page=50&order=asc'
        else: day_url=f'{base}/fixtures/between/{start.isoformat()}/{end.isoformat()}?{FIXTURE_INCLUDES}&per_page=50&order=asc'
        merged={}
        for raw in _sportmonks_get_all(day_url,token):
            f=_normalise_fixture(raw)
            if f.get('id') is not None: merged[str(f['id'])]=f
        # The livescores endpoint holds only in-play fixtures, so it is only
        # needed when the requested range includes today.
        if end==today:
            for raw in (_sportmonks_get(f'{base}/livescores?{FIXTURE_INCLUDES}',token).get('data') or []):
                f=_normalise_fixture(raw)
                if f.get('id') is not None: merged[str(f['id'])]=f
        fixtures=sorted(merged.values(),key=lambda f:(f.get('date') or '',f.get('time') or '99:99',(f.get('home') or '').lower()))
        return jsonify(fixtures=fixtures,updated_at=datetime.now(UK_TZ).strftime('%d %b %Y %H:%M'),source='sportmonks',**meta)
    except urllib.error.HTTPError as e:
        detail=''
        try: detail=e.read().decode('utf-8','replace')
        except Exception: pass
        return jsonify(error=f'Sportmonks returned HTTP {e.code}. {detail[:300]}'),502
    except urllib.error.URLError as e: return jsonify(error=f'Could not connect to Sportmonks: {e.reason}'),502
    except Exception as e: return jsonify(error=f'Sportmonks request failed: {e}'),502


TABLE_LEAGUES={8:'Premier League',9:'Championship',12:'League One',14:'League Two'}

def _normalise_detail_token(value):
    return ''.join(ch.lower() for ch in str(value or '') if ch.isalnum())

def _detail_value(detail):
    value=detail.get('value')
    if isinstance(value,dict):
        for key in ('value','total','count','amount'):
            if value.get(key) is not None:
                value=value.get(key); break
    if value is not None:
        try: return int(float(value))
        except Exception: pass
    return None

def _standing_num(row,key,default=0):
    """Read a numeric standings field from Sportmonks, including detail objects."""
    # 1. Direct field.
    v=row.get(key)
    if v is not None:
        try: return int(float(v))
        except Exception: pass

    # 2. Common nested overall object.
    overall=row.get('overall') or {}
    if isinstance(overall,dict):
        for candidate in (key, 'matches_played' if key in ('played','overall_matches') else key,
                          'overall_matches' if key in ('played','overall_matches') else key):
            v=overall.get(candidate)
            if v is not None:
                try: return int(float(v))
                except Exception: pass

    # 3. Sportmonks `details` entries. The exact spelling can vary by API
    # representation, so compare normalized developer/code/name values.
    details=row.get('details') or []
    if isinstance(details,dict): details=[details]
    wanted={
        'played': {'overallmatches','overallmatchesplayed','matchesplayed','overallgamesplayed','gamesplayed'},
        'overall_matches': {'overallmatches','overallmatchesplayed','matchesplayed','overallgamesplayed','gamesplayed'},
        'points': {'points','overallpoints'},
        'position': {'position','overallposition'},
        'goal_diff': {'goaldifference','goaldiff','overallgoaldifference'},
        'goal_difference': {'goaldifference','goaldiff','overallgoaldifference'},
    }.get(key,{_normalise_detail_token(key)})
    for detail in details:
        if not isinstance(detail,dict): continue
        typ=detail.get('type') or {}
        tokens=[]
        if isinstance(typ,dict):
            tokens += [typ.get('developer_name'),typ.get('code'),typ.get('name'),typ.get('label')]
        tokens += [detail.get('developer_name'),detail.get('code'),detail.get('name'),detail.get('label')]
        norm={_normalise_detail_token(x) for x in tokens if x}
        type_id=str(detail.get('type_id') or '')
        # 129 is Sportmonks' overall-matches-played type in the current
        # standings representation, but the textual match is preferred.
        if norm & wanted or (key in ('played','overall_matches') and type_id=='129'):
            value=_detail_value(detail)
            if value is not None: return value

    return default

def _normalise_standing(row):
    p=row.get('participant') or row.get('team') or {}
    return {'position':_standing_num(row,'position',0),'name':p.get('name') or row.get('name') or row.get('team_name') or '', 'played':_standing_num(row,'played',_standing_num(row,'overall_matches',0)), 'points':_standing_num(row,'points',0),'goal_diff':_standing_num(row,'goal_diff',_standing_num(row,'goal_difference',0))}

def _mock_table(league_id):
    names={8:['Arsenal','Manchester City','Manchester United','Liverpool','Aston Villa','Chelsea','Newcastle United','Tottenham Hotspur','Crystal Palace','Everton','Fulham','Brentford','Brighton & Hove Albion','Bournemouth','Nottingham Forest','West Ham United','Leeds United','Wolverhampton Wanderers','Burnley','Sheffield United'],9:['Leeds United','Middlesbrough','Coventry City','Southampton','West Bromwich Albion','Hull City','Millwall','Norwich City','Burnley','Bristol City','Sheffield United','Swansea City','Watford','Preston North End','Stoke City','Blackburn Rovers','Derby County','Cardiff City','Oxford United','Portsmouth','QPR','Hull City','Plymouth Argyle','Rotherham United'],12:['Stockport County','Wigan Athletic','Bolton Wanderers','Huddersfield Town','Barnsley','Charlton Athletic','Reading','Lincoln City','Blackpool','Mansfield Town','Wycombe Wanderers','Leyton Orient','Peterborough United','Rotherham United','Stevenage','Exeter City','Bristol Rovers','Northampton Town','Burton Albion','Cambridge United','Crawley Town','Shrewsbury Town','Burton Albion','Barnsley'],14:['Chesterfield','Notts County','Walsall','Bradford City','Salford City','MK Dons','Swindon Town','Crewe Alexandra','Gillingham','Colchester United','Grimsby Town','Bromley','Tranmere Rovers','Barrow','Accrington Stanley','Fleetwood Town','Cheltenham Town','Newport County','Harrogate Town','Morecambe','Crawley Town','Carlisle United','Shrewsbury Town','Port Vale']}[league_id]
    rows=[]
    n=len(names)
    for i,name in enumerate(names,1): rows.append({'position':i,'name':name,'played':35,'points':max(20,90-i*3),'goal_diff':max(-30,55-i*4)})
    return {'league_id':league_id,'name':TABLE_LEAGUES[league_id],'rows':rows,'updated_at':datetime.now(UK_TZ).strftime('%d %b %Y %H:%M'),'source':'mock'}

def _sportmonks_standings(league_id):
    token=os.getenv('SPORTMONKS_API_TOKEN')
    if not token: raise RuntimeError('SPORTMONKS_API_TOKEN is not configured in the service environment.')
    base='https://api.sportmonks.com/v3/football'
    req=urllib.request.Request(f'{base}/leagues/{league_id}?include=currentSeason;seasons&api_token={urllib.parse.quote(token)}',headers={'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as resp: league_payload=json.loads(resp.read().decode('utf-8'))
    league=(league_payload.get('data') or {})
    # Sportmonks has returned this relationship under different casing in
    # API representations. Prefer the explicitly current season, then fall
    # back to the league's seasons list and select is_current=true.
    season=(league.get('currentSeason') or league.get('current_season') or league.get('currentseason') or {})
    season_id=season.get('id') if isinstance(season,dict) else None
    if not season_id:
        seasons=league.get('seasons') or []
        if isinstance(seasons,dict): seasons=seasons.get('data') or []
        if isinstance(seasons,list):
            current=[x for x in seasons if isinstance(x,dict) and x.get('is_current') is True]
            if current:
                season_id=current[0].get('id')
            else:
                usable=[x for x in seasons if isinstance(x,dict) and x.get('id') and not x.get('finished') and not x.get('pending')]
                usable.sort(key=lambda x:str(x.get('starting_at') or ''), reverse=True)
                if usable: season_id=usable[0].get('id')
    if not season_id:
        raise RuntimeError(f'No current season found for {TABLE_LEAGUES.get(league_id,"selected league")}')
    url=f'{base}/standings/seasons/{season_id}?include=participant;details.type&api_token={urllib.parse.quote(token)}'
    req=urllib.request.Request(url,headers={'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as resp: payload=json.loads(resp.read().decode('utf-8'))
    rows=[_normalise_standing(x) for x in (payload.get('data') or [])]
    rows=[r for r in rows if r['name']]
    rows.sort(key=lambda r:r['position'])
    return {'league_id':league_id,'season_id':season_id,'name':league.get('name') or TABLE_LEAGUES.get(league_id,'League'),'rows':rows,'updated_at':datetime.now(UK_TZ).strftime('%d %b %Y %H:%M'),'source':'sportmonks'}

@app.get('/api/standings')
@app.get('/api/league-table')
def api_standings():
    try: league_id=int(request.args.get('league_id','8'))
    except: league_id=8
    if league_id not in TABLE_LEAGUES:return jsonify(error='Unsupported league.'),400
    if request.args.get('mode')=='test':return jsonify(_mock_table(league_id))
    try:return jsonify(_sportmonks_standings(league_id))
    except urllib.error.HTTPError as e:
        detail=''
        try: detail=e.read().decode('utf-8','replace')
        except: pass
        return jsonify(error=f'Sportmonks returned HTTP {e.code}. {detail[:300]}'),502
    except urllib.error.URLError as e:return jsonify(error=f'Could not connect to Sportmonks: {e.reason}'),502
    except Exception as e:return jsonify(error=str(e)),502


@app.get('/result-font')
def result_font():
    return send_file(FONT_PATH, mimetype='font/ttf')

@app.get('/studio-logo')
def studio_logo():
    return send_file(STUDIO_LOGO_PATH, mimetype='image/png')

@app.get('/')
def index(): return render_template('index.html', app_version=APP_VERSION)

@app.get('/health')
def health():
    return jsonify(
        ok=True,
        version=APP_VERSION,
        ffmpeg=shutil.which('ffmpeg') is not None,
        elevenlabs=bool(os.getenv('ELEVENLABS_API_KEY') and os.getenv('ELEVENLABS_VOICE_ID')),
        elevenlabs_model=os.getenv('ELEVENLABS_MODEL','eleven_multilingual_v2'),
        sportmonks=bool(os.getenv('SPORTMONKS_API_TOKEN'))
    )

@app.get('/api/live-scores/test-empty')
def api_live_scores_test_empty():
    return jsonify(fixtures=[],updated_at=datetime.now(UK_TZ).strftime('%d %b %Y %H:%M'),source='mock-empty')

@app.post('/preview')
def preview():
    data=clean_results(request.get_json(force=True)); im=make_frame(data,None,1.0)
    from io import BytesIO
    b=BytesIO(); im.save(b,'JPEG',quality=82, optimize=True);
    return jsonify(image='data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode('ascii'))


@app.post('/preview-table')
def preview_table():
    data=request.get_json(force=True) or {}; league=data.get('league') or {}; rows=league.get('rows') or []
    if not rows:return jsonify(error='Load a league table first.'),400
    data['name']=league.get('name') or 'League'; data['league_id']=league.get('league_id', league.get('id', data.get('league_id',0))); data['rows']=rows; data['endLogo']=bool(data.get('endLogo',True)); data['scene']=data.get('scene','top')
    im=make_table_frame(data,None,1.0,data['scene']); from io import BytesIO; b=BytesIO(); im.save(b,'JPEG',quality=84,optimize=True); return jsonify(image='data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode('ascii'))

@app.post('/generate-table')
def generate_table():
    data=request.get_json(force=True) or {}; league=data.get('league') or {}; rows=league.get('rows') or []
    if not rows:return jsonify(error='Load a league table first.'),400
    data['name']=league.get('name') or 'League'; data['league_id']=league.get('league_id', league.get('id', data.get('league_id',0))); data['rows']=rows; data['includeVoiceover']=bool(data.get('includeVoiceover',True)); data['mentionPoints']=bool(data.get('mentionPoints',True)); data['includeIntroMusic']=bool(data.get('includeIntroMusic',True)); data['endLogo']=bool(data.get('endLogo',True)); data['mid90sLoopBackground']=bool(data.get('mid90sLoopBackground',False))
    job_id=uuid.uuid4().hex[:12]
    with LOCK:JOBS[job_id]={'status':'queued','progress':1,'message':'Queued league table render…'}
    threading.Thread(target=render_table_job,args=(job_id,data),daemon=True).start(); return jsonify(id=job_id)


@app.post('/preview-printer')
def preview_printer():
    data=clean_leagues(request.get_json(force=True)); im=make_printer_frame(data,0,None,False)
    from io import BytesIO
    b=BytesIO(); im.save(b,'JPEG',quality=84,optimize=True)
    return jsonify(image='data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode('ascii'))

@app.post('/generate-printer')
def generate_printer():
    data=clean_leagues(request.get_json(force=True))
    if not any(x.get('results') for x in data['leagues']): return jsonify(error='Add at least one result first.'),400
    job_id=uuid.uuid4().hex[:12]
    with LOCK:JOBS[job_id]={'status':'queued','progress':1,'message':'Queued Vidi Printer render…'}
    out=OUT/_render_filename(data,'printer')
    threading.Thread(target=lambda: _printer_job(job_id,data,out),daemon=True).start()
    return jsonify(id=job_id)

def _printer_job(job_id,data,out):
    with RENDER_LOCK:
        try:
            with LOCK:JOBS[job_id].update(status='rendering',progress=3,message='Rendering Results Vidi Printer…')
            render_printer_video(data,out,job_id)
            with LOCK:JOBS[job_id].update(status='done',progress=100,message='Results Vidi Printer video ready.',path=str(out))
        except Exception as e:
            print('PRINTER RENDER ERROR',repr(e),flush=True)
            with LOCK:JOBS[job_id].update(status='error',progress=100,message=f'Vidi Printer render failed: {e}')

@app.post('/generate')
def generate():
    data=clean_leagues(request.get_json(force=True))
    if not data['leagues'] or not data['leagues'][0]['results']: return jsonify(error='Add at least one result to Results 1.'),400
    if len(data['leagues'])==2 and not data['leagues'][1]['results']: return jsonify(error='Results 2 is enabled but has no results.'),400
    try:
        validate_results(data)
    except ValueError as e:
        return jsonify(error=str(e)),400
    job_id=uuid.uuid4().hex[:12]
    with LOCK: JOBS[job_id]={'status':'queued','progress':1,'message':'Queued…'}
    threading.Thread(target=render_job,args=(job_id,data),daemon=True).start()
    return jsonify(id=job_id)

@app.get('/status/<job_id>')
def status(job_id):
    with LOCK: j=JOBS.get(job_id)
    if not j: return jsonify(status='error',message='Job not found.'),404
    return jsonify({k:v for k,v in j.items() if k!='path'})

@app.get('/download/<job_id>')
def download(job_id):
    with LOCK: j=JOBS.get(job_id)
    if not j or j.get('status')!='done': abort(404)
    return send_file(j['path'],as_attachment=True,download_name=Path(j['path']).name,mimetype='video/mp4')

if __name__=='__main__':
    port=int(os.getenv('PORT','10000'))
    app.run(host='0.0.0.0',port=port,threaded=True)
