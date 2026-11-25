"""Offline only: retain phase resets and raw coordinates, never infer total travel."""
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1])]
last=None;direction=None;segments=[];gaps=[]
for row in rows:
 if row.get('kind')=='trace_gap':gaps.append(row);last=None;direction=None;continue
 if row.get('kind')!='reply' or row.get('method')!='get_feed_info':continue
 lanes=row.get('data',{}).get('feed_info',[])
 if len(lanes)!=4:continue
 item=lanes[3];value=item.get('decoder')
 if item.get('_decoder_present') is not True or type(value) is not int:last=None;direction=None;continue
 if value>=1<<63:value-=1<<64
 if last is None:
  segments.append({'direction':'unknown','start_time':row['time'],'first':value,'last':value,'min':value,'max':value});last=value;continue
 new_direction='increasing' if value>last else 'reverse_or_reset' if value<last else direction
 if new_direction!=direction and new_direction is not None:
  segments.append({'direction':new_direction,'start_time':row['time'],'first':last,'last':value,'min':min(last,value),'max':max(last,value)})
 elif segments:
  segment=segments[-1];segment.update(last=value,min=min(segment['min'],value),max=max(segment['max'],value),end_time=row['time'])
 direction=new_direction;last=value
print(json.dumps({'target':3,'segments':segments,'trace_gaps':gaps,'total_distance':'unknown','note':'Coordinates are phase-local observations; reverse and counter reset are not distinguished.'},indent=2))
