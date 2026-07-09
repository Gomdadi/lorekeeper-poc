# Indexing 변형 비교 리포트

| variant | chunks | 라벨별 노드 | 관계 타입별 | pruned(n/r/p) | resolve(대상/병합) | 토큰(req/resp/total) | 소요(초) |
|---|---|---|---|---|---|---|---|
| v_baseline | 37 | Character:40, CharacterState:42, Chunk:37, Event:54, Location:14 | APPEARS_IN:129, ESTABLISHED_IN:39, FROM_CHUNK:228, HAS_STATE:43, HOSTS:17, LOCATED_IN:5, NEXT_CHUNK:36 | 0/7/0 | 228/54 | 178765/15535/194300 | 39.6 |
| v_recursive | 37 | Character:33, CharacterState:47, Chunk:37, Event:52, Location:13 | APPEARS_IN:128, ESTABLISHED_IN:43, FROM_CHUNK:226, HAS_STATE:48, HOSTS:14, LOCATED_IN:1, NEXT_CHUNK:36 | 0/12/0 | 226/46 | 178460/15938/194398 | 40.0 |
| v_kiwi | 34 | Character:30, CharacterState:44, Chunk:34, Event:42, Location:14 | APPEARS_IN:114, ESTABLISHED_IN:41, FROM_CHUNK:203, HAS_STATE:43, HOSTS:16, LOCATED_IN:2, NEXT_CHUNK:33 | 0/2/0 | 203/44 | 165538/14023/179561 | 35.5 |
| v_kss | 33 | Character:28, CharacterState:35, Chunk:33, Event:39, Location:16 | APPEARS_IN:104, ESTABLISHED_IN:35, FROM_CHUNK:191, HAS_STATE:32, HOSTS:24, NEXT_CHUNK:32 | 0/1/0 | 191/44 | 161141/12922/174063 | 34.6 |
| v_resolver_embed | 37 | Character:34, CharacterState:47, Chunk:37, Event:50, Location:14 | APPEARS_IN:125, ESTABLISHED_IN:47, FROM_CHUNK:224, HAS_STATE:47, HOSTS:24, LOCATED_IN:4, NEXT_CHUNK:36 | 0/3/0 | 127/17 | 178765/15645/194410 | 54.2 |
| v_resolver_fuzzy | 37 | Character:29, CharacterState:47, Chunk:37, Event:55, Location:9 | APPEARS_IN:130, ESTABLISHED_IN:47, FROM_CHUNK:234, HAS_STATE:45, HOSTS:19, LOCATED_IN:3, NEXT_CHUNK:36 | 0/9/0 | 132/17 | 178765/16290/195055 | 36.2 |
| v_kss_fuzzy | 33 | Character:28, CharacterState:35, Chunk:33, Event:46, Location:12 | APPEARS_IN:116, ESTABLISHED_IN:35, FROM_CHUNK:200, HAS_STATE:34, HOSTS:14, LOCATED_IN:6, NEXT_CHUNK:32 | 0/6/0 | 120/17 | 161141/13948/175089 | 38.1 |
