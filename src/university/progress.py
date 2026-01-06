"""
MeshForge University - Progress Tracking

Tracks user progress through courses and assessments.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


@dataclass
class LessonProgress:
    """Progress for a single lesson"""
    lesson_id: str
    completed: bool = False
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    assessment_score: Optional[float] = None
    time_spent_seconds: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'lesson_id': self.lesson_id,
            'completed': self.completed,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'assessment_score': self.assessment_score,
            'time_spent_seconds': self.time_spent_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LessonProgress':
        return cls(
            lesson_id=data['lesson_id'],
            completed=data.get('completed', False),
            started_at=data.get('started_at'),
            completed_at=data.get('completed_at'),
            assessment_score=data.get('assessment_score'),
            time_spent_seconds=data.get('time_spent_seconds', 0),
        )


@dataclass
class CourseProgress:
    """Progress for a course"""
    course_id: str
    lessons: Dict[str, LessonProgress] = field(default_factory=dict)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @property
    def completed_count(self) -> int:
        return sum(1 for lp in self.lessons.values() if lp and lp.completed)

    @property
    def total_count(self) -> int:
        return sum(1 for lp in self.lessons.values() if lp is not None)

    @property
    def percentage(self) -> float:
        if self.total_count == 0:
            return 0.0
        return (self.completed_count / self.total_count) * 100

    @property
    def is_complete(self) -> bool:
        return self.total_count > 0 and self.completed_count == self.total_count

    @property
    def average_score(self) -> Optional[float]:
        scores = [lp.assessment_score for lp in self.lessons.values() if lp and lp.assessment_score is not None]
        if not scores:
            return None
        return sum(scores) / len(scores)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'course_id': self.course_id,
            'lessons': {k: v.to_dict() for k, v in self.lessons.items() if v is not None},
            'started_at': self.started_at,
            'completed_at': self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CourseProgress':
        lessons = {}
        for lesson_id, lesson_data in data.get('lessons', {}).items():
            if lesson_data is not None and isinstance(lesson_data, dict):
                try:
                    lessons[lesson_id] = LessonProgress.from_dict(lesson_data)
                except (KeyError, TypeError):
                    pass  # Skip corrupted lesson data
        return cls(
            course_id=data['course_id'],
            lessons=lessons,
            started_at=data.get('started_at'),
            completed_at=data.get('completed_at'),
        )


class ProgressTracker:
    """Tracks and persists learning progress"""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = get_real_user_home() / '.config' / 'meshforge'
        self.config_dir = config_dir
        self.progress_file = config_dir / 'university_progress.json'
        self.courses: Dict[str, CourseProgress] = {}
        self._load()

    def _load(self):
        """Load progress from disk"""
        if self.progress_file.exists():
            try:
                data = json.loads(self.progress_file.read_text())
                for course_id, course_data in data.get('courses', {}).items():
                    self.courses[course_id] = CourseProgress.from_dict(course_data)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error loading progress: {e}")

    def _save(self):
        """Save progress to disk"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            'courses': {k: v.to_dict() for k, v in self.courses.items()},
            'last_updated': datetime.now().isoformat(),
        }
        self.progress_file.write_text(json.dumps(data, indent=2))

    def get_course_progress(self, course_id: str) -> CourseProgress:
        """Get progress for a course, creating if needed"""
        if course_id not in self.courses:
            self.courses[course_id] = CourseProgress(course_id=course_id)
        return self.courses[course_id]

    def start_lesson(self, course_id: str, lesson_id: str):
        """Mark a lesson as started"""
        course = self.get_course_progress(course_id)
        if course.started_at is None:
            course.started_at = datetime.now().isoformat()

        if lesson_id not in course.lessons:
            course.lessons[lesson_id] = LessonProgress(lesson_id=lesson_id)

        lesson = course.lessons[lesson_id]
        if lesson.started_at is None:
            lesson.started_at = datetime.now().isoformat()

        self._save()

    def complete_lesson(self, course_id: str, lesson_id: str, score: Optional[float] = None):
        """Mark a lesson as completed"""
        course = self.get_course_progress(course_id)

        if lesson_id not in course.lessons:
            course.lessons[lesson_id] = LessonProgress(lesson_id=lesson_id)

        lesson = course.lessons[lesson_id]
        lesson.completed = True
        lesson.completed_at = datetime.now().isoformat()
        if score is not None:
            lesson.assessment_score = score

        # Check if course is now complete
        if course.is_complete and course.completed_at is None:
            course.completed_at = datetime.now().isoformat()

        self._save()

    def update_time_spent(self, course_id: str, lesson_id: str, seconds: int):
        """Update time spent on a lesson"""
        course = self.get_course_progress(course_id)
        if lesson_id in course.lessons:
            course.lessons[lesson_id].time_spent_seconds += seconds
            self._save()

    def get_overall_stats(self) -> Dict[str, Any]:
        """Get overall learning statistics"""
        total_lessons = 0
        completed_lessons = 0
        total_time = 0
        scores = []

        for course in self.courses.values():
            total_lessons += course.total_count
            completed_lessons += course.completed_count
            for lesson in course.lessons.values():
                if lesson is not None:
                    total_time += lesson.time_spent_seconds
                    if lesson.assessment_score is not None:
                        scores.append(lesson.assessment_score)

        return {
            'total_courses': len(self.courses),
            'completed_courses': sum(1 for c in self.courses.values() if c.is_complete),
            'total_lessons': total_lessons,
            'completed_lessons': completed_lessons,
            'total_time_hours': total_time / 3600,
            'average_score': sum(scores) / len(scores) if scores else None,
        }

    def reset_progress(self, course_id: Optional[str] = None):
        """Reset progress for a course or all courses"""
        if course_id:
            if course_id in self.courses:
                del self.courses[course_id]
        else:
            self.courses.clear()
        self._save()
