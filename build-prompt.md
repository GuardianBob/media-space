Build media manager: media collection database and interface.
 
Media manager should have following features:
- media database
- media browser
    - display:
        - poster
        - Title
        - Summary
        - actors
        - link to trailer
        - file location (hotlink to open directory containing file)
    - Read connected jellyfin database to collect and update media information for owned media.
    - Ability for user to search for media (See how Seer does this) for user to manually add to owned media collection
    - Ability for user to update media collection by uploading csv file with list of media (title, year, (*optional column), country) to bulk update collection database.
    - Ability (with verification) for user to delete movie from database.
    - Ability to scan user-specfied directory for media to import (such as if user has media on portable drive) so that it saves media info as if it were manually entered.
- Analyze Seer app (https://github.com/seerr-team/seerr):Use as many features as possible from Seer code for all app features
- identify how Seer connects to Jellyfin server and pulls and displays movie/tv/anime information and posters.
- integrate media browser feature from seer to enable browsing media in owed media collection and searching for media to manually add.


tTask: Build a Media Manager application with a web/desktop interface and a backend database.

Technical Requirements:

    Backend: A database capable of handling media metadata (Poster, Title, Summary, Actors, Trailer Link, File Path).
    Sync Engine: Connect to Jellyfin API to fetch and synchronize owned media data.
    Import Tools:
        A directory scanner that detects new files on external drives and maps them to the DB.
        A CSV upload tool for bulk updates (Title, Year, Country). 
    UI Features:
        A "Media Browser" for owned items.
        A search interface for discovering new media (reference Seer's discovery logic).
        A "Hotlink" button that opens the local file directory in the OS file explorer. 
    Safety: Implement a "Verify before Delete" workflow. 

Reference Logic:
Use the seerr-team/seerr GitHub as the primary reference for:

    Jellyfin API connection logic (how it fetches posters/info).
    The search/discovery UI flow.
    The layout of the media browser. 

Constraint: Ensure the "hotlink" to local directories works correctly across different operating systems. 

Project dir: "/home/jbear33/Documents/Coding/media-space"

Ask me questions until you are 85% positive you can complete the task