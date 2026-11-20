MATCH (u:User {id: $entity})<-[:OWNED_BY]-(p:Post)<-[:ON_POST]-(v:Vote)
WHERE u.available_at < $cutoff
  AND p.available_at < $cutoff
  AND v.available_at < $cutoff
RETURN u.id AS user_id, p.id AS post_id, v.id AS vote_id
ORDER BY vote_id
