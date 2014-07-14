USE unittest;
DROP TABLE IF EXISTS unittest.contents;
CREATE TABLE unittest.contents (
     forum_post_id       varchar(40),
     anon_screen_name    varchar(40),
     type                varchar(20),
     anonymous           varchar(10),
     anonymous_to_peers  varchar(10),
     at_position_list    varchar(200),
     forum_int_id        bigint(20) unsigned,
     body                varchar(2500),
     course_display_name varchar(100),
     created_at          datetime,
     votes               varchar(200),
     count               int(11),
     down_count          int(11),
     up_count            int(11),
     up                  varchar(200),
     down                varchar(200),
     comment_thread_id   varchar(255),
     parent_id           varchar(255),
     parent_ids          varchar(255),
     sk                  varchar(255),
     confusion           varchar(20),
     happiness           varchar(20)
     );

INSERT INTO unittest.contents
       	(forum_post_id,anon_screen_name,type,anonymous,anonymous_to_peers,at_position_list,forum_int_id,body,
	 course_display_name,created_at,votes,count,down_count,up_count,up,down,comment_thread_id,parent_id,
	 parent_ids,sk,confusion,happiness) 
VALUES("519461545924670200000001","<anon_screen_name_redacted>","CommentThread","False","False","[]",11,
	"First forum entry.","MITx/6.002x/2012_Fall","2013-05-16 04:32:20",
	"{'count': 10, 'point': -6, 'down_count': 8, 'up': ['2', '10'], 'down': ['1', '3', '4', '5', '6', '7', '8', '9'], 'up_count': 2}",
	10,8,2,"['2', '10']","['1', '3', '4', '5', '6', '7', '8', '9']","None","None",
	"None","None","none","none"),
      ("519461545924670200000005","<anon_screen_name_redacted>","Comment","False","False","[]",7,
       "Second forum entry.","MITx/6.002x/2012_Fall","2013-05-16 04:32:20",
       "{'count': 10, 'point': 4, 'down_count': 3, 'up': ['1', '2', '5', '6', '7', '8', '9'], 'down': ['3', '4', '10'], 'up_count': 7}",
       10,3,7,"['1', '2', '5', '6', '7', '8', '9']","['3', '4', '10']",
       "519461545924670200000001","None","[]","519461545924670200000005","none","none")